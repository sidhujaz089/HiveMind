#!/usr/bin/env python3
"""auto_merge.py -- merge a local model's build into main ONLY when every check is mechanical and green.

Jas, 2026-10-01: "I trust your judgment so let's do the auto merge." The rule Claude proposed
(session-review finding ccbda03bacef): one file, changes confined to what the brief names,
merge_gate and merge_accounting clean, the oracle flips red -> green, no new unresolved names --
anything else comes to Claude. Every condition below is a check, never a judgement, and every
refusal says which one failed. A branch that fails any of them stays in `reviewq.py` for Claude.

    python3 scripts/auto_merge.py            # dry run: what would merge, and why not the rest
    python3 scripts/auto_merge.py --merge    # merge the ones that pass, record each verdict

THE CONDITIONS, in the order they are checked (cheapest first):
  1. ONE FILE. The branch changes exactly one file, and it is not under scripts/tests/.
  2. A BRIEF, A CHUNK, AN ORACLE. The branch's brief is found (file, or the copy the branch
     carries since t311), its work-list row names a target suite, and that suite exists on the
     merge base -- the oracle was written BEFORE the build, never by it.
  3. NARROW. Every changed hunk lies inside a symbol the brief names on its SYMBOLS: line
     (spans read from the base file's AST). A brief with no SYMBOLS line is not narrow enough.
  4. CLEAN SHAPE (scripts/code_shape.py, the lessons of the 17 rejected branches): against
     CURRENT main the change introduces no name bound nowhere and removes no top-level
     definition the brief does not name.
  5. RED -> GREEN. The oracle fails on the merge base and passes on the branch.
  6. merge_gate.review(): the oracle untouched, and green against current main with every
     other failure declared.
  7. The file is not dirty in the working tree (the merge must not tangle with live edits).

A merge is recorded as a `kept` verdict in reviewq with why="auto-merged: ...", so escapes are
still tracked (`reviewq.py escaped`) and an escape after an auto-merge shows up in the Pareto.
"""
import argparse
import ast
import difflib
import json
import time
import re
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "projects" / "local-inference"))

AGENT = "claude-code/auto-merge"


def git(*args, cwd=None, timeout=300):
    r = subprocess.run(["git", "-C", str(cwd or REPO), *args], capture_output=True, text=True,
                       timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def show(rev, path):
    rc, out, _ = git("show", f"{rev}:{path}")
    return out if rc == 0 else None


def brief_text(branch):
    import merge_accounting as MA
    p = MA.brief_for_branch(branch)
    if p and p.is_file():
        return p.name, p.read_text(encoding="utf-8", errors="replace")
    kept = MA.brief_from_commit(branch)
    return (None, kept) if kept else (None, None)


def target_for(brief_name, text):
    """The chunk's target suite: its work-list row, else the brief's FROZEN ORACLE line."""
    if brief_name:
        for line in (REPO / "scripts" / "autonomy_loop.sh").read_text(encoding="utf-8").splitlines():
            parts = line.split("|")
            if len(parts) == 4 and parts[1].strip() == brief_name:
                return parts[3].strip()
    m = re.search(r"FROZEN ORACLE:\s*(scripts/tests/test_\w+\.py)", text or "")
    return m.group(1) if m else None


def symbol_spans(src, names):
    """[(lo, hi)] line spans (1-based, inclusive, decorators included) of the named symbols.

    NAMED MODULE-LEVEL CONSTANTS COUNT (2026-10-19, Part A.1). Only defs and classes were spanned,
    so a brief naming FINISH_TAIL or LEDGER on its SYMBOLS line was refused for editing exactly
    that constant (~30 delivered branches stuck on "outside the named symbols")."""
    spans = []
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            lo = min([node.lineno] + [d.lineno for d in node.decorator_list])
            if node.name in names:
                spans.append((lo, node.end_lineno))
            if isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if (isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and (f"{node.name}.{sub.name}" in names
                                 # A BARE METHOD NAME RESOLVES (4(A), 2026-10-22): the scoper's
                                 # prompt teaches bare names, so SYMBOLS: check for Gate.check
                                 # matched nothing, every hunk inside the method read as outside,
                                 # and 4 live branches died here. A name colliding across two
                                 # classes only ADDS spans, and only for a name the brief
                                 # genuinely wrote -- the method is defined in this file either
                                 # way, which is the guarantee the symbols rule exists for.
                                 or sub.name in names)):
                        slo = min([sub.lineno] + [d.lineno for d in sub.decorator_list])
                        spans.append((slo, sub.end_lineno))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id in names for t in targets):
                spans.append((node.lineno, node.end_lineno))
    return spans


def defined_names(src):
    """Every top-level name a file defines (defs, classes, Class.method, assigned names)."""
    out = set()
    for node in ast.parse(src).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
            if isinstance(node, ast.ClassDef):
                out |= {f"{node.name}.{s.name}" for s in node.body
                        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))}
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            out |= {t.id for t in targets if isinstance(t, ast.Name)}
    return out


def new_symbol_spans(base_src, branch_src, names):
    """Branch-side spans of the NAMED symbols the build CREATED (Part A.2). A brief that names
    roll_up_triage and asks for it to be written is refused today because a new symbol has no
    base-side span; it is inside if it is named and did not exist before."""
    created = set(names) - defined_names(base_src)
    return symbol_spans(branch_src, created) if created else []


def tolerated_spans(src):
    """Branch-side spans of top-level imports and UPPER_CASE constant assignments (whole statements,
    so a constant written over several lines counts). What outside_tolerance allows line by line,
    found by the parser so it can share one inserted block with a new named symbol (2026-10-20:
    coder/brief-362fa23a added OWNER_PRIORITY, NEXT_ACTOR, TASK_ID, QUEUE beside its two named new
    functions in one hunk, and the whole block read as outside)."""
    out = []
    try:
        tree = ast.parse(src or "")
    except SyntaxError:
        return out
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.append((node.lineno, node.end_lineno))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            # A PRIVATE UPPER_CASE CONSTANT IS A MODULE CONSTANT (4(C), 2026-10-22):
            # _CLASSIFIER_NO_VERDICT, _MISS_DIRECTIONS, _IMPORT_STDOUT are excluded by their
            # leading underscore. The docstring's reason for refusing lowercase names -- a
            # lowercase module statement executes at import and can change behaviour -- does not
            # apply to an underscore-prefixed ALL_CAPS name, which is what module-private
            # constants are. Lowercase stays refused, with the underscore or without.
            if targets and all(isinstance(t, ast.Name)
                               and re.fullmatch(r"_{0,2}[A-Z][A-Z0-9_]*|__all__", t.id)
                               for t in targets):
                out.append((node.lineno, node.end_lineno))
    return out


def hunks_outside(base, branch, path, spans, new_spans=(), branch_src=""):
    """Base-side line ranges of hunks that fall outside every span. A pure insertion whose
    code lines all lie within a newly created named symbol (new_spans) is inside -- blank lines and
    comments in the same hunk are not code."""
    blines = branch_src.split("\n")
    rc, out, _ = git("diff", "-U0", f"{base}..{branch}", "--", path)
    bad = []
    for m in re.finditer(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? ", out, re.M):
        start, n = int(m.group(1)), int(m.group(2) if m.group(2) is not None else 1)
        lo, hi = (start, start + max(n, 1) - 1) if n else (start, start)
        if any(a <= lo and hi <= b for a, b in spans):
            continue
        if n == 0:
            bstart, bn = int(m.group(3)), int(m.group(4) if m.group(4) is not None else 1)
            blo, bhi = bstart, bstart + max(bn, 1) - 1
            # blank and comment lines do not execute: a new symbol's explanatory comment above it is
            # part of adding it, not an edit outside the named symbols (2026-10-19, 1c509bbe/7d3ed409)
            code = [k for k in range(blo, bhi + 1)
                    if k - 1 >= len(blines) or (blines[k - 1].strip()
                                                and not blines[k - 1].lstrip().startswith("#"))]
            allowed = list(new_spans) + tolerated_spans(branch_src)
            # THE EMPTY CASE IS THE INTENDED CASE (4(D), 2026-10-22): `code and` inverted the
            # comment above -- a hunk whose added lines are ALL blank and comment adds nothing
            # that executes, so it is inside by that comment's own logic, not outside. The
            # incidents named in 4-automerge-rca.md (1c509bbe, 7d3ed409) died exactly here:
            # a scoper's explanatory comment above its new symbol, landed as its own hunk,
            # refused. The vacuous-all guard: with no code lines there is nothing to check
            # against `allowed`, and `bn and` keeps a zero-line hunk refused as before.
            if bn and (not code
                       or all(any(a <= k <= b for a, b in allowed) for k in code)):
                continue
        bad.append(f"{lo}-{hi}")
    return bad


# Mirrors tolerated_spans (4(C)): an UPPER_CASE constant counts with its leading underscore --
# module-private constants (_MAX_ITEMS, _CLASSIFIER_NO_VERDICT) are as inert at import as the
# public ones. Lowercase stays refused: it executes and can change behaviour.
_TOLERATED_ADD = re.compile(r"import\s+\w|from\s+\w[\w.]*\s+import\b|__all__\s*=|_{0,2}[A-Z][A-Z0-9_]*\s*=")


def outside_tolerance(base_src, branch_src, outside):
    """True when every change inside the reported ranges ADDS only top-level imports, __all__,
    or UPPER_CASE constants (blank lines allowed beside them) -- Part A.3 / t1072.

    Only changes that touch a reported range are judged: an edit inside a named symbol was already
    accepted by the span check, so a build that edits its named function AND adds the import it
    needs is tolerated. Inside a reported range a replacement or deletion is refused (it edits
    existing code), as are indented lines, lowercase module statements, and new defs."""
    if not outside:
        return False
    ranges = []
    for r in outside:
        a, _, b = str(r).partition("-")
        ranges.append((int(a), int(b) if b else int(a)))
    base_lines, branch_lines = base_src.split("\n"), branch_src.split("\n")
    judged = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, base_lines, branch_lines,
                                                       autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            touches = any(lo <= i1 <= hi for lo, hi in ranges)  # git -U0 keys an insert on the line before
        else:
            touches = any(lo <= i2 and i1 + 1 <= hi for lo, hi in ranges)
        if not touches:
            continue
        if tag != "insert":
            return False
        judged += 1
        kinds = [bool(_TOLERATED_ADD.match(ln)) for ln in branch_lines[j1:j2] if ln.strip()]
        if not kinds or not all(kinds):
            return False
    return judged > 0


def suite_result(rev, suite):
    """Exit code of `suite` run in a throwaway worktree of `rev` (None if it cannot be run)."""
    wt = Path(tempfile.mkdtemp(prefix="automerge-"))
    try:
        if git("worktree", "add", "-q", "--detach", str(wt), rev)[0] != 0:
            return None
        if not (wt / suite).is_file():
            return None
        try:
            r = subprocess.run([sys.executable, str(wt / suite)], cwd=str(wt), capture_output=True,
                               text=True, timeout=600)
            return r.returncode
        except subprocess.TimeoutExpired:
            return None
    finally:
        git("worktree", "remove", "--force", str(wt))
        shutil.rmtree(wt, ignore_errors=True)


POLICY_MARK = "POLICY:"


def brief_files(text):
    """The paths a brief names on its `FILES:` line, or []. t3196, 2026-10-25.

    Condition 1 used to be "exactly one file", which refused a zero-risk build that touches the two
    files its own brief names -- so the automatic path covered less than the class it exists for and
    the rest went to a person. When a brief states its FILES, every changed file must be on that line;
    when it does not, the old one-file rule stands unchanged, so no existing branch is judged
    differently by this change.
    """
    m = re.search(r"^\s*FILES:\s*(.+)$", text or "", re.M)
    if not m:
        return []
    return [x.strip().strip("`") for x in re.split(r"[,\s]+", m.group(1)) if x.strip()]


def files_allowed(files, target, named, existed=None):
    """(ok, reason). The oracle is never the builder's to change, named or not.

    NO EXISTING TEST EITHER (L1, Phase 0b-2, 2026-10-27). With a FILES line only the oracle was
    refused by path, so a build could name and weaken a neighbouring suite and merge unread. A file
    under scripts/tests/ is allowed only when it is named AND did not exist at the merge base -- the
    brief's own new test. ``existed(path)`` answers that; without it every test is refused.
    """
    if any(f == target for f in files):
        return False, (f"the oracle {target} is changed by the build -- the oracle is never the "
                       f"model's to change")
    if not named:
        if len(files) != 1:
            return False, (f"changes {len(files)} files and the brief names no FILES line; "
                           f"auto-merge takes exactly one")
        if files[0].startswith("scripts/tests/"):
            return False, "the one file is a test -- the oracle is never the model's to change"
        return True, ""
    old_tests = [f for f in files if f.startswith("scripts/tests/") and (existed is None or existed(f))]
    if old_tests:
        return False, (f"changes existing test(s) {', '.join(old_tests)} -- only the brief's own NEW "
                       f"test may be in an auto-merge")
    extra = [f for f in files if f not in named]
    if extra:
        return False, (f"changes {len(extra)} file(s) the brief does not name: {', '.join(extra)}")
    if not files:
        return False, "changes nothing"
    return True, ""


def policy_lines_from(lines):
    """The lines carrying the POLICY marker, out of an iterable of (added or removed) lines."""
    return [ln for ln in lines if POLICY_MARK in ln]


def policy_lines(base, branch, files):
    """Every line the branch ADDS or REMOVES that carries the POLICY marker (t3196).

    No amount of narrowness makes a policy change mechanical. A one-file, symbol-narrow, red->green
    diff can still flip a default that IS a policy -- a role's model, a pause flag, a retention
    window -- and that reaches a person whatever else holds.
    """
    rc, out, _ = git("diff", "-U0", f"{base}..{branch}", "--", *files)
    if rc != 0:
        return []
    return policy_lines_from(ln[1:] for ln in out.splitlines()
                             if ln[:1] in "+-" and not ln.startswith(("+++", "---")))


def judge(branch, run_gate=True):
    """(ok, reasons, facts). ok only when every condition holds."""
    import code_shape
    facts = {"branch": branch}
    rc, base, _ = git("merge-base", "main", branch)
    base = base.strip()
    if rc != 0 or not base:
        return False, ["no merge base with main"], facts
    rc, out, _ = git("diff", "--name-only", f"{base}..{branch}")
    files = [f for f in out.split() if f]
    facts["files"] = files
    bname, text = brief_text(branch)                                       # 2
    named = brief_files(text)                                              # 1 (t3196)
    facts["named"] = named
    ok_files, why_files = files_allowed(files, target_for(bname, text) if text else "", named,
                                        lambda f: show(base, f) is not None)
    if not ok_files:
        return False, [why_files], facts
    path = files[0]
    if not text:
        return False, ["no brief found for this branch (file gone, none carried in the commit)"], facts
    target = target_for(bname, text)
    facts["target"] = target
    # A COMPLIANCE ORACLE ONLY RE-RUNS THE CHECK (t341/t411). It goes green when the flagged
    # pattern disappears -- including when the code moves somewhere nothing reaches (p41: chatui's
    # live log tail moved to a file nothing writes; check green, panel permanently empty). Nothing
    # mechanical can tell that from a real fix, so a compliance build is always read by Claude.
    if target and Path(target).name.startswith("test_comply_"):
        return False, ["a compliance oracle only re-runs the check -- a fix that removes the "
                       "behaviour passes too, so it is read, not auto-merged (t341)"], facts
    if not target:
        return False, ["no target suite for this brief (no work-list row, no FROZEN ORACLE line)"], facts
    if show(base, target) is None:
        return False, [f"the oracle {target} did not exist before the build"], facts
    import coder
    names = coder.symbols_of(text)                                         # 3
    if not names:
        return False, ["the brief names no SYMBOLS -- not narrow enough to merge unread"], facts
    before = show(base, path)
    if before is None or not path.endswith(".py"):
        return False, [f"{path} is new or not Python -- outside what this gate can read"], facts
    try:
        spans = symbol_spans(before, set(names))
    except SyntaxError as e:
        return False, [f"the base {path} does not parse: {e}"], facts
    branch_src = show(branch, path) or ""
    try:
        new_spans = new_symbol_spans(before, branch_src, set(names))
    except SyntaxError as e:
        return False, [f"the branch {path} does not parse: {e}"], facts
    outside = hunks_outside(base, branch, path, spans, new_spans, branch_src)
    if outside and not outside_tolerance(before, branch_src, outside):
        return False, [f"hunk(s) at base line(s) {', '.join(outside)} lie outside the named "
                       f"symbols {', '.join(names)}"], facts
    for extra in files[1:]:                                                # t3196: every named file
        before_x = show(base, extra)
        if before_x is None or not extra.endswith(".py"):
            return False, [f"{extra} is new or not Python -- outside what this gate can read"], facts
        try:
            spans_x = symbol_spans(before_x, set(names))
            bx_src = show(branch, extra) or ""
            new_x = new_symbol_spans(before_x, bx_src, set(names))
        except SyntaxError as e:
            return False, [f"{extra} does not parse: {e}"], facts
        out_x = hunks_outside(base, branch, extra, spans_x, new_x, bx_src)
        if out_x and not outside_tolerance(before_x, bx_src, out_x):
            return False, [f"{extra}: hunk(s) at base line(s) {', '.join(out_x)} lie outside the "
                           f"named symbols {', '.join(names)}"], facts
    pol = policy_lines(base, branch, files)                                # t3196 fence
    if pol:
        return False, [f"the diff touches a POLICY line, which no mechanical gate may decide: "
                       f"{pol[0].strip()[:120]}"], facts
    for f in files:                                                        # 4, every file (L1 0b-2)
        if not f.endswith(".py"):
            continue
        shape = code_shape.regressions(show("main", f), show(branch, f) or "", text)
        if shape:
            return False, [f"{f}: {r}" for r in shape], facts
    rc_dirty = git("status", "--porcelain", "--", path)[1].strip()          # 7 (cheap, so early)
    if rc_dirty:
        return False, [f"{path} has uncommitted changes in the working tree"], facts
    red, green = suite_result(base, target), suite_result(branch, target)  # 5
    facts.update(oracle_at_base=red, oracle_on_branch=green)
    if red in (None, 0):
        return False, [f"the oracle does not fail on the merge base (exit {red}) -- nothing proves "
                       f"the change is what made it pass"], facts
    if green != 0:
        return False, [f"the oracle does not pass on the branch (exit {green})"], facts
    if run_gate:                                                           # 6
        import merge_gate
        g = merge_gate.review(branch, verbose=False)
        if g.get("verdict") != "MERGEABLE":
            return False, ["merge_gate: " + "; ".join(g.get("problems") or ["not mergeable"])], facts
    return True, [], facts


# ONE MERGE INTO MAIN AT A TIME (08-l2-round-plan.md, L1 item 3). Four lanes and this gate all move
# main; two merges racing can silently lose one. merge_lock.py is the flock every merger takes, and
# this is its first caller on the automated path. A busy lock is a skip, not a failure: the branch
# stays as it is and the next tick tries again.
LOCK_WAIT_S = 120


# ---------------------------------------------------------------- FRONTIER BRANCHES (2026-10-28)
# Jas, 2026-10-28, on Claude's proposal: "you write it and claude will sign it. 6 is better than
# nothing and when we get more data we can see how to expand that."
#
# WHY: auto-merge has only ever judged local-model `coder/brief-*` builds, because it needs a BRIEF, a
# SYMBOLS list and a pre-written oracle. A frontier fix (`dsh/*`, `claude/*`) has none of those, so it was
# never checked however small it was, and every one of them waited for a Claude lane -- an escalation by
# our own definition. Six of the ~40 merges since 2026-10-26 were like this.
#
# THE CONDITIONS, each a check and never a judgement, and each refusing with its own words:
#   F1. NOT THE MERGE RULES THEMSELVES. The branch touches none of the six guarded files. A builder must
#       never approve its own change to the two-signature rule, so those still go to a family for review.
#       (This is why no author-family exclusion is needed here: guarded work cannot pass F1 at all, and for
#       non-guarded work no signature is due -- the checks below ARE the review.)
#   F2. SOMETHING PROVES IT. A test added or changed on the branch FAILS on the MERGE BASE and PASSES on the
#       branch. (The base, not today's main: the property being proved is that this change is what turns
#       the test green. F5 and F6 cover the tree it actually merges into.) A DOCS-ONLY diff (every path a .md) is exempt, and "docs-only" is decided by the DIFF:
#       a branch cannot exempt itself by saying so.
#   F3. IT MAY NOT EDIT AN EXISTING TEST. A test that existed at the merge base may not be touched, or a
#       branch could satisfy F2 by deleting the test that would fail.
#   F4. THE CEILING DOES NOT RISE. Its allowed-to-fail list may not grow.
#   F5. IT MERGES CLEANLY (`git merge-tree`): the conflict that left a checkout half-merged is refused
#       before the merge is attempted.
#   F6. THE WORKSPACE CHECK IS NO WORSE, run ONCE on the merged tree in a scratch worktree with a bound.
#       It is not re-run before and after per candidate: measured 2026-10-28, the check took over ten
#       minutes under load, and running it twice per branch would make this the box's largest consumer.
#       If it cannot finish, the answer is NO -- an answer nobody could get is not evidence.
GUARDED = ("scripts/memory_guard.py", "scripts/auto_merge.py", "scripts/hooks/",
           "scripts/role_gate.py", "projects/app-repo/taskstore.py", "scripts/dispatch_loop.py")
FRONTIER_PREFIXES = ("dsh/", "claude/")
FRONTIER_LEDGER = REPO / "workspace" / "run-logs" / "auto-merge-frontier.jsonl"


def frontier_shape(branch):
    """A frontier branch is one a lane wrote by hand: dsh/* or claude/*. Nothing else is considered."""
    return bool(branch) and str(branch).startswith(FRONTIER_PREFIXES)


def _guarded_touched(files):
    return [f for f in files if any(f == g or (g.endswith("/") and f.startswith(g)) for g in GUARDED)]


def worktree_branches():
    """Branch names currently checked out in ANY worktree (including the main one).

    `git worktree list --porcelain` prints "branch refs/heads/<name>" for each checkout; a branch listed
    there has an editor open on it somewhere, so it is not finished by definition.
    """
    rc, out, err = git("worktree", "list", "--porcelain")
    if rc != 0:
        print(f"  NOTE: could not list worktrees ({err.strip()}); no branch excluded", file=sys.stderr)
        return set()
    names = set()
    for line in (out or "").splitlines():
        if line.startswith("branch refs/heads/"):
            names.add(line[len("branch refs/heads/"):].strip())
    return names


def frontier_branches():
    """Unmerged refs named dsh/* or claude/*, asked of git rather than of the review ledger.

    Claude's review, 2026-10-28: "IT NEVER FIRES ON ITS OWN: main() only enumerates reviewq.pending()
    branches, which are coder/brief-*. dsh/* and claude/* are reached only with --branch, so the tick will
    judge zero frontier branches." It was right: a reviewer that is never handed the branch is not a gate,
    it is a manual command with extra steps.
    """
    rc, out, err = git("for-each-ref", "--format=%(refname:short)", "refs/heads/")
    if rc != 0:
        print(f"  NOTE: could not list branches ({err.strip()}); no frontier branch judged", file=sys.stderr)
        return []
    in_use = worktree_branches()
    found = []
    for name in (out or "").split():
        if not frontier_shape(name):
            continue
        if name.endswith("/"):
            continue
        if name in in_use:
            # A BRANCH IN A WORKTREE IS BEING WORKED ON (Claude, 2026-10-28: "a WIP branch whose new test
            # happens to flip green could merge before its author is done"). Judging every unmerged ref is
            # the point; merging a half-finished one is not. Said out loud, because a silent skip would be
            # a branch nobody judges for reasons nobody can see.
            print(f"  NOTE: {name} is checked out in a worktree -- being worked on, not judged", file=sys.stderr)
            continue
        a = git("merge-base", "--is-ancestor", name, "main")
        if a[0] == 0:
            continue                        # already in main: not work
        found.append(name)
    return sorted(found)


def judge_frontier(branch, base=None, suite_at=None, merge_tree=None, check_after=None):
    """(ok, why, facts) for a frontier branch. The four expensive steps are injectable for the suite."""
    base = base or git("merge-base", "main", branch)[1].strip()
    changed = [f for f in git("diff", "--name-only", base, branch)[1].split() if f]
    if not changed:
        return False, ("FRONTIER: the branch changes nothing against its merge base"), {}
    guarded = _guarded_touched(changed)
    if guarded:
        return False, (f"FRONTIER F1: touches the merge rules themselves ({', '.join(guarded)}) -- "
                       f"a two-signature file goes to a family, never to this script"), {}
    docs_only = all(f.endswith(".md") for f in changed)
    tests = [f for f in changed if f.startswith("scripts/tests/") and f.endswith(".py")]
    old = [f for f in tests if not git("cat-file", "-e", f"{base}:{f}")[0]]
    if old:
        return False, (f"FRONTIER F3: edits a test that already existed ({', '.join(old)}) -- a branch "
                       f"may not delete the test that would fail"), {}
    if not docs_only and not tests:
        return False, ("FRONTIER F2: nothing proves it -- no test was added or changed, and the diff is "
                       "not docs-only"), {}
    # F4: the ceiling may not rise.
    def allowed(rev):
        import json as _json
        t = show(rev, "workspace/test-failures-ceiling.json")
        try:
            return len((_json.loads(t or "{}")).get("allowed_to_fail") or [])
        except ValueError:
            return None
    before, after = allowed("main"), allowed(branch)
    if before is None or after is None:
        return False, "FRONTIER F4: the ceiling could not be read on one side -- not compared", {}
    if after > before:
        return False, f"FRONTIER F4: the ceiling rises ({before} -> {after})", {}
    # F2: the test must be RED on main and GREEN on the branch.
    flips = []
    if not docs_only:
        run = suite_at or suite_result
        for t in tests:
            rc_base = run(base, t)
            if rc_base is None:
                return False, f"FRONTIER F2: {t} could not be run on the base -- not assumed", {}
            if rc_base == 0:
                return False, (f"FRONTIER F2: {t} already passes on main, so it proves nothing about "
                               f"this branch"), {}
            rc_branch = run(branch, t)
            if rc_branch is None:
                return False, f"FRONTIER F2: {t} could not be run on the branch -- not assumed", {}
            if rc_branch != 0:
                return False, f"FRONTIER F2: {t} still fails on the branch", {}
            flips.append(t)
    # F5: clean merge.
    mt = merge_tree or (lambda b: git("merge-tree", "--write-tree", "main", b))
    rc, out, err = mt(branch)
    if rc != 0:
        return False, f"FRONTIER F5: git merge-tree shows a conflict: {(out + err).strip()}", {}
    # F6: the workspace check, once, on the merged tree.
    chk = check_after or _check_merged
    ok, detail = chk(branch)
    if not ok:
        return False, f"FRONTIER F6: {detail}", {}
    return True, "", {"files": changed, "docs_only": docs_only, "tests": flips, "base": base}


def new_failures(baseline, after):
    """Checks FAILING after the merge that were NOT failing before it. Main's standing failures are the
    baseline, so a branch that inherits them is fine and a branch that ADDS one is not."""
    return sorted(set(after or ()) - set(baseline or ()))


_MAIN_TALLY = {}                      # {main sha: set(failing check names)} -- one run per main, cached


def _fail_names(repo):
    """(ok, set(names), detail) from one check_workspace --quick run. Never a count: names compare."""
    r = subprocess.run([sys.executable, str(Path(repo) / "scripts" / "check_workspace.py"), "--quick"],
                       cwd=str(repo), capture_output=True, text=True, timeout=1500)
    names = set(re.findall(r"\[FAIL\]\s+(.+)", r.stdout))
    m = re.search(r"(\d+) passed, \d+ warning\(s\), (\d+) failed", r.stdout)
    if not m:
        return False, set(), "the workspace check printed no tally -- not assumed"
    # THE FLAG IS ALWAYS TRUE ON PURPOSE: this ran, so the comparison can be made. Whether the merged tree
    # is acceptable is decided by the fail NAMES, not by this count (Claude, 2026-10-28: "int(...) == 0 or
    # True -- always True; harmless since callers ignore it, but it reads like a bug"). It read like a bug
    # because it was written as an expression that pretends to test something.
    return True, names, f"{m.group(1)} passed, {m.group(2)} failed"


def _check_merged(branch, timeout=1500, repo=None):
    """(ok, detail): no check FAILS on the merged tree that does not already fail on main.

    Claude's review, 2026-10-28: "F6 is 'green', not 'no worse': it refuses on any failed check, and main
    has 2 standing FAILs right now (ceiling; 42 unrecorded verdicts), so nothing can pass." Correct -- a
    condition nothing can satisfy is not a condition, it is a block wearing one. Main's failing check
    NAMES are the baseline (cached per main SHA, so it is still one run per candidate), and the merge may
    not ADD one.
    """
    repo = Path(repo or REPO)
    rc, sha, _ = git("rev-parse", "main")
    main_sha = (sha or "").strip()
    if main_sha not in _MAIN_TALLY:
        try:
            _, names, detail = _fail_names(repo)
            _MAIN_TALLY[main_sha] = names
        except subprocess.TimeoutExpired:
            return False, f"the workspace check on main did not finish within {timeout}s -- not assumed"
    baseline = _MAIN_TALLY[main_sha]
    wt = Path.home() / ".cache" / "hivemind-tmp" / "auto-merge-frontier-wt"
    try:
        if wt.exists():
            git("worktree", "remove", "--force", str(wt))
        rc, out, err = git("worktree", "add", "--detach", str(wt), "main")
        if rc != 0:
            return False, f"could not make a scratch worktree: {(out + err).strip()}"
        rc, out, err = git("-C", str(wt), "merge", "--no-ff", "--no-commit", branch)
        if rc != 0:
            git("-C", str(wt), "merge", "--abort")
            return False, f"the merge itself failed in the scratch tree: {(out + err).strip()}"
        try:
            _, after, detail = _fail_names(wt)
        except subprocess.TimeoutExpired:
            return False, f"the workspace check did not finish within {timeout}s -- not assumed"
        new = new_failures(baseline, after)
        if new:
            return False, (f"the merged tree FAILS {len(new)} check(s) main does not: " + "; ".join(new))
        return True, f"{detail}; no check newly failing against main's {len(baseline)}"
    finally:
        try:
            git("worktree", "remove", "--force", str(wt))
        except Exception as e:                                     # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: the scratch worktree {wt} could not be removed ({type(e).__name__}: {e}); "
                  f"the next run removes it", file=sys.stderr)


def record_frontier(branch, merged, why, facts, would_have_gone_to="claude"):
    """One row per frontier decision, WITH the counter Jas asked for: it names who it would have gone to."""
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "branch": str(branch),
           "merged": bool(merged), "why": str(why),
           "tests": (facts or {}).get("tests") or [], "docs_only": bool((facts or {}).get("docs_only")),
           "would_have_gone_to": would_have_gone_to}
    try:
        FRONTIER_LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with FRONTIER_LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as e:
        # THE COUNTER IS THE POINT OF THIS CHANGE, so a failure to record it is said out loud: a silent
        # gap in this ledger would make "auto-merge removes escalations" unfalsifiable.
        print(f"  NOTE: could not record the frontier decision for {branch}: {e}", file=sys.stderr)


def merge(branch, facts, lock_timeout=LOCK_WAIT_S, frontier=False):
    import merge_lock
    try:
        with merge_lock.hold("auto-merge", f"merge {branch}", timeout=lock_timeout):
            return _merge_locked(branch, facts, frontier=frontier)
    except merge_lock.LockTimeout as e:
        h = e.args[0] if e.args else None
        who = f"{h.get('lane')} ({h.get('why')}, since {h.get('since')})" if isinstance(h, dict) else "another merger"
        return False, f"merge lock held by {who} for over {lock_timeout:g} s -- not merged, next tick retries"


def _merge_locked(branch, facts, frontier=False):
    task = None
    rows = [r for r in __import__("reviewq").pending() if r.get("branch") == branch]
    if rows:
        task = rows[-1].get("task")
    if frontier:
        files = facts.get("files") or []
        msg = (f"Merge {branch} ({task or 'frontier fix'}) -- auto-merged by the frontier gate\n\n"
               f"Every frontier condition in scripts/auto_merge.py held: it touches none of the six "
               f"guarded files ({len(files)} file(s): {', '.join(files)}), "
               + ("docs only" if facts.get("docs_only") else
                  f"its test(s) {', '.join(facts.get('tests') or [])} failed on main and pass on the branch")
               + ", the ceiling did not rise, git merge-tree showed no conflict, and the workspace "
                 "check is green on the merged tree.\n\nAgent: %s\n" % AGENT)
    else:
        msg = (f"Merge {branch} ({task or 'brief'}) -- auto-merged by the gate\n\n"
               f"Every condition in scripts/auto_merge.py held: one file ({facts['files'][0]}), hunks "
               f"inside the brief's SYMBOLS, no new unbound names or unnamed removals, oracle "
               f"{facts['target']} red on the base and green on the branch, merge_gate MERGEABLE.\n\n"
               f"Agent: {AGENT}\n")
    # BUILT ELSEWHERE, MOVED BY REF (t9562, 2026-10-29). `git merge` here ran in the LIVE checkout, where ~1,250
    # dirty paths made git autostash the tree; a merge that then refused spent ~40 minutes re-applying the stash and
    # left "Index was not unstashed". merge_reviewed builds the same merge commit in a clean worktree and moves main
    # by `update-ref` under the lock this caller already holds (locked=True), never touching the dirty tree.
    import merge_reviewed
    merge_reviewed.git = globals()["git"]           # one git for both modules: this repo's, and a test's fake
    try:
        new, paths = merge_reviewed.build(branch, "", None, "", "", msg_text=msg)
        merge_reviewed.move_main(new, paths, locked=True)
    except SystemExit as e:
        return False, str(e)
    import reviewq
    reviewq.main(["verdict", "--branch", branch, "kept", "--why",
                  f"auto-merged: every auto_merge.py condition held (oracle {facts['target']})"])
    # A MERGED BRANCH IS DELETED (2026-10-14): left in place it kept its task reading "DELIVERED,
    # waiting on Claude's review" -- ~20 merged tasks sat on Claude that way. -d refuses an unmerged one.
    rc, out, err = git("branch", "-d", branch)
    if rc != 0:
        print(f"  NOTE: merged {branch} not deleted: {(out + err).strip()}", file=sys.stderr)
    return True, ""


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--merge", action="store_true", help="merge what passes (default: report only)")
    ap.add_argument("--branch", help="judge one branch")
    a = ap.parse_args(argv)
    # ONE AT A TIME. The dispatcher starts this every tick, detached; a run that is still in
    # merge_gate's suites must not be joined by a second one merging the same branch.
    import fcntl
    lockf = open(REPO / "workspace" / "run-logs" / "auto-merge.lock", "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("  auto-merge: another run holds the lock -- not starting a second.")
        return 0
    import reviewq
    branches = [a.branch] if a.branch else sorted(
        {r["branch"] for r in reviewq.pending()
         if r.get("branch") and reviewq.branch_exists(r["branch"])}
        | set(frontier_branches()))          # (1) the tick must MEET a frontier branch to judge it
    if not branches:
        print("  auto-merge: nothing delivered is waiting.")
        return 0
    for b in branches:
        frontier = False
        ok, why, facts = judge(b)
        if not ok and frontier_shape(b):
            # A FRONTIER BRANCH HAS NO BRIEF, so the coder judge refuses it by construction. It gets the
            # second judge instead.
            fok, fwhy, ffacts = judge_frontier(b)
            if not fok:
                print(f"  [for Claude] {b}: {fwhy}")
                record_frontier(b, False, fwhy, ffacts)     # the escalation this change did NOT remove
                continue
            ok, why, facts, frontier = True, "", ffacts, True
        if not ok:
            print(f"  [for Claude] {b}: {why[0]}")
            continue
        if not a.merge:
            print(f"  [would merge{' (frontier)' if frontier else ''}] {b}")
            if frontier:
                record_frontier(b, False, "dry run: would merge", facts)
            continue
        done, err = merge(b, facts, frontier=frontier)
        print(f"  [{'MERGED' if done else 'merge failed'}] {b}" + ("" if done else f": {err}"))
        if frontier:
            # (3) RECORD AFTER THE MERGE, WITH THE REAL RESULT. Claude's review: "the counter:
            # record_frontier is called with merged=False before the merge, so a branch that merges is
            # logged as not merged." It was: a counter that lies about its own event is worse than none.
            record_frontier(b, bool(done), "merged" if done else f"merge failed: {err}", facts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
