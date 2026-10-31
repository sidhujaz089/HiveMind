#!/usr/bin/env python3
"""Refuse a merge into main unless the committer's ROLE has that merge's task on its list (t476).

    python3 scripts/role_gate.py commit-msg <message-file>     # called by scripts/hooks/commit-msg
    python3 scripts/role_gate.py lane-owns <message-file>      # ditto: the four lanes' file ownership
    python3 scripts/role_gate.py ref-update                    # stdin of reference-transaction
    python3 scripts/role_gate.py who "<Agent trailer value>"   # which role that trailer maps to

WHY. Jas, 2026-09-29, on the DeepSeek Harness: "I just really want to make sure that its
permissions are set properly so I can't do something it shouldn't be allowed to." ROLES.json gave
the Harness an empty task list and said "Merge review stays off so it cannot merge by accident" --
but nothing read that list. commit-msg exits 0 on any subject beginning "Merge ", so a `git merge`
by anyone went straight in.

WHO IS COMMITTING comes from the commit's own `Agent: <tool>/<model>` trailer, the one commit-msg
already requires. A role claims trailers by its `trailers` field in ROLES.json (a tool, like
"dsh-harness", or a full "human/jas"); a model role also claims "ollama/<its model>". A trailer no
role claims is REFUSED for a merge -- absence of a mapping is not permission (L-146).

DIRECT COMMITS (Jas, 2026-09-30): "Yes build the direct commits to main task." A commit made straight
on main that is NOT a merge -- edit, commit -- skipped the whole pipeline (preflight, the build, the
merge review), and the gate did not look at it: the DeepSeek Harness could not merge, but it could
commit code to main directly. Such a commit now needs `direct_commit` on the committer's role list:
Claude has it; the Harness only while Jas switches it on (as with merging); Jas and the model roles
do not (models reach main only through gated merges). Commits on any other branch are not gated.
If ROLES.json cannot be read, a direct commit falls back to Claude's own trailers (DIRECT_FALLBACK)
and SAYS so -- otherwise a broken role store would refuse the commit that repairs it.

WHAT COUNTS AS A MERGE INTO MAIN:
  * a commit on main with MERGE_HEAD, SQUASH_MSG or CHERRY_PICK_HEAD present, of anything but main;
  * main being moved (fast-forward, `branch -f`, reset) onto the tip of a coder/* or bridge/* branch. That makes
    no commit, so commit-msg never runs; reference-transaction does, and it has no trailer to read,
    so this is refused for EVERYONE: a build reaches main only as a merge commit someone signs.

THE HONEST LIMIT. These are git hooks. `--no-verify`, or unsetting core.hooksPath, skips them, and
the Harness runs as the same Unix user as everyone else. This stops a merge by mistake or by a
confused agent -- which is the failure Jas described -- not a determined one. Ordinary commits are
not classified into tasks yet (a commit does not say which task it served); see t476's notes.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ROLES = REPO / "projects" / "local-inference" / "models" / "ROLES.json"
MERGE_TASK = "merge_review"
DIRECT_TASK = "direct_commit"
DIRECT_FALLBACK = ("claude-code", "claude-chat")     # only while ROLES.json is unreadable
GATED_BRANCH = "main"
# WHICH TASK A MERGE SERVES, by the branch being merged. coder/* is a delivered build (Claude's
# merge review, D-34). bridge/* is the manager's ORACLE FREEZE, which bridge_trigger.py merges on
# its own, signed ollama/<manager model> (D-35 as amended 2026-09-28) -- that is scoping, and
# gating it as a merge review would have stopped the pipeline. Anything else: merge_review.
BRANCH_TASKS = (("refs/heads/coder/", MERGE_TASK), ("refs/heads/bridge/", "scoping"))
ZERO = "0" * 40
CONFIG_FILES = ("projects/local-inference/models/ROLES.json",
                "projects/local-inference/models/ROLES-CHANGELOG.jsonl",
                "projects/local-inference/models/SERVING.json")
AGENT_RE = re.compile(r"^Agent:\s*([A-Za-z0-9._-]+/[A-Za-z0-9._:-]+)\s*$", re.MULTILINE)
# TWO-SIGNATURE FILES (06-prompts-final.md, L2 Phase 0b, 2026-10-27). The six a confused change to
# can hurt every lane at once. A merge that brings one of them needs a second model to have
# looked; the rule was prose until now, and prose is what L3 bypassed when it merged 47603d93d and
# asked afterwards.
GUARDED = ("scripts/memory_guard.py", "scripts/auto_merge.py", "scripts/hooks/",
           "scripts/role_gate.py", "projects/app-repo/taskstore.py", "scripts/dispatch_loop.py")
# TWO GROUPS, ALWAYS: callers unpack (reviewer, scope). A signature may name more than one scope --
# L3 wrote "(diff + suite-run)", which is a truer claim than either term alone, and the first version
# of this accepted only one term and refused it. The second group is the whole scope expression.
REVIEW_RE = re.compile(r"^Reviewed-by:\s*([A-Za-z0-9._-]+/[A-Za-z0-9._:-]+)\s*"
                       r"\(((?:diff|oracle|suite-run|live)"
                       r"(?:\s*\+\s*(?:diff|oracle|suite-run|live))*)\)\s*$", re.MULTILINE)
LANE_RE = re.compile(r"^Lane:\s*(L[1-4])\s*$", re.MULTILINE)


def git(*args):
    r = subprocess.run(["git", *args], capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout.strip()


def load_roles(path=None):
    try:
        return json.loads(Path(path or ROLES).read_text(encoding="utf-8")).get("roles", {})
    except (OSError, ValueError):
        return {}


def role_for(agent, roles):
    """The role key an Agent trailer value belongs to, or None. Longest claim wins."""
    agent = (agent or "").strip()
    tool = agent.split("/", 1)[0]
    best, best_len = None, -1
    for key, r in roles.items():
        claims = list(r.get("trailers") or [])
        if r.get("kind") == "model" and r.get("model"):
            claims.append(f"ollama/{r['model']}")
        for c in claims:
            if (c == agent or c == tool) and len(c) > best_len:
                best, best_len = key, len(c)
    return best


def has_task(role, task):
    return any((t.get("task") if isinstance(t, dict) else t) == task
               for t in (role.get("tasks") or []))


def refusal(agent, key, roles, what, task=MERGE_TASK):
    lines = ["", f"  REFUSED BY THE ROLE GATE (t476) -- {what}.", ""]
    if not agent:
        lines.append("  The commit has no 'Agent: <tool>/<model>' trailer, so there is no role to check.")
    elif key is None:
        lines.append(f"  No role in ROLES.json claims 'Agent: {agent}', and an unknown committer")
        lines.append(f"  may not make {what}.")
    else:
        name = roles[key].get("name", key)
        tasks = [t.get("task") if isinstance(t, dict) else t for t in roles[key].get("tasks") or []]
        lines.append(f"  'Agent: {agent}' is the {name} role, whose tasks are: "
                     f"{', '.join(tasks) or '(none)'}.")
        lines.append(f"  This -- {what} -- needs '{task}' on that list.")
    # THE HINT MUST MATCH THE REASON (2026-10-25). This line printed for EVERY refusal, so a commit
    # with NO Agent trailer told the committer to "add 'direct_commit'" -- a permission they may
    # already have, on a role it never identified. It sent a session down that path twice and cost a
    # person a trip to the Models tab to grant something granted the day before. When the role is
    # known and merely lacks the task, the permission hint is right; otherwise it is noise that
    # hides the real reason, which in that case is the missing trailer.
    if key is None:
        lines += ["", "  To allow it: add the trailer the role gate can read, as the last line of the",
                  "  commit message -- 'Agent: <tool>/<model>', e.g. 'Agent: claude-code/opus-5'.",
                  "  Nothing was committed.", ""]
    else:
        lines += ["", f"  To allow it: the Hub's Models tab -> Roles -> the role -> Tasks -> add '{task}'",
                  "  (or scripts/role_store.py). Nothing was committed.", ""]
    return "\n".join(lines)


def on_main():
    rc, branch = git("symbolic-ref", "-q", "--short", "HEAD")
    return rc == 0 and branch == GATED_BRANCH


def task_for_merge():
    """The task the commit being made needs, or None when it is not a merge onto main.

    A merge of a branch tip -> that branch's task (BRANCH_TASKS). A squash, a cherry-pick, or a
    merge of anything no branch points at cannot be traced to a branch -> merge_review.
    """
    rc, branch = git("symbolic-ref", "-q", "--short", "HEAD")
    if rc != 0 or branch != GATED_BRANCH:
        return None
    rc, head = git("rev-parse", "-q", "--verify", "MERGE_HEAD")
    if rc == 0:
        _, refs = git("for-each-ref", "--points-at", head, "--format=%(refname)", "refs/heads/")
        needs = {task for ref in refs.splitlines() for pre, task in BRANCH_TASKS
                 if ref.startswith(pre)}
        return needs.pop() if len(needs) == 1 else MERGE_TASK
    for name in ("SQUASH_MSG", "CHERRY_PICK_HEAD"):
        rc, p = git("rev-parse", "--git-path", name)
        if rc == 0 and Path(p).exists():
            return MERGE_TASK
    return None


def _live_checkout_branch(msg_file):
    """An AGENT role may not commit on a branch in the LIVE checkout -- only in a worktree (2026-10-09).

    The DeepSeek Harness, unable to commit to main, did `git checkout -b dsh/...` in ~/ai-workspace
    itself and left it there. The services commit to whatever the live checkout has checked out, so
    the pipeline's own bridge/t764 commits landed on DSH's branch, and main's newest session log went
    missing from the tree (a dangling-reference FAIL). Branches belong in `git worktree add`, which
    leaves the live checkout on main. Models and people are untouched: this reads kind == "agent".
    """
    rc, gd = git("rev-parse", "--git-dir")
    rc2, cd = git("rev-parse", "--git-common-dir")
    if rc or rc2 or Path(gd).resolve() != Path(cd).resolve():
        return 0                                   # a linked worktree: branches are what it is for
    text = Path(msg_file).read_text(encoding="utf-8", errors="replace")
    m = AGENT_RE.search(text)
    roles = load_roles()
    key = role_for(m.group(1), roles) if (m and roles) else None
    if not key or roles[key].get("kind") != "agent" or key == "claude":
        return 0
    _, branch = git("symbolic-ref", "-q", "--short", "HEAD")
    print(f"  REFUSED: {roles[key].get('name', key)} is committing on branch '{branch}' in the LIVE "
          f"checkout.\n  The services commit to whatever this folder has checked out, so a branch here "
          f"captures their work.\n  Do this instead:\n"
          f"    git checkout main            # put the live checkout back\n"
          f"    git worktree add ~/ai-workspace-dsh/<topic> -b dsh/<topic> main\n"
          f"  and commit there. Claude merges dsh/* branches (reviewq.py lists them).",
          file=sys.stderr)
    return 1


def check_commit(msg_file):
    task = task_for_merge()
    what = f"a merge into {GATED_BRANCH}"
    if task is None:
        if not on_main():
            return _live_checkout_branch(msg_file)
        task, what = DIRECT_TASK, f"a direct commit to {GATED_BRANCH} (not a merge)"
    text = "\n".join(l for l in Path(msg_file).read_text(encoding="utf-8", errors="replace")
                     .splitlines() if not l.startswith("#"))
    m = AGENT_RE.search(text)
    agent = m.group(1) if m else ""
    roles = load_roles()
    if not roles and task == DIRECT_TASK and agent.split("/", 1)[0] in DIRECT_FALLBACK:
        print(f"  NOTE: ROLES.json is unreadable, so the direct-commit gate fell back to Claude's own "
              f"trailers ({agent}). Repair the role store.", file=sys.stderr)
        return 0
    key = role_for(agent, roles) if agent else None
    if key and has_task(roles[key], task):
        return 0
    # A PERSON'S MODELS-TAB EDITS COMMIT THEMSELVES (Jas, 2026-10-08: "I didn't know you needed to
    # commit changes to the model settings. Can that be automated? I am not sure why you need to be in
    # the loop on config changes to the role."). config_autocommit.py commits ONLY the role store's
    # files, signed by the person who made the edit; nothing else may ride along.
    if task == DIRECT_TASK and key and roles[key].get("kind") == "person":
        rc, staged = git("diff", "--cached", "--name-only")
        files = set(staged.split())
        if rc == 0 and files and files <= set(CONFIG_FILES):
            return 0
    # ONE MODEL, MANY ROLES (2026-10-08). `ollama/<model>` names a model, not a role; when several
    # roles run the same model, role_for picks the first, and a freeze was refused for 18 hours
    # because that role lacked Scoping while another role on the same model had it. Any role
    # running the signing model that has the task may sign.
    if agent.startswith("ollama/") and any(
            r.get("kind") == "model" and f"ollama/{r.get('model')}" == agent and has_task(r, task)
            for r in roles.values()):
        return 0
    # A FROZEN SCOPE HELD BACK FOR REVIEW (2026-10-01). bridge/* is normally merged by the gates as
    # scoping; when the freeze is off, or the scope waits on Claude, the merge is a REVIEW of it --
    # so a role with Merge review may sign it too. Nothing else changes: coder/* still needs merge_review.
    if key and task == "scoping" and has_task(roles[key], MERGE_TASK):
        return 0
    print(refusal(agent, key, roles, what, task), file=sys.stderr)
    return 1


def _staged_files():
    rc, out = git("diff", "--cached", "--name-only", "--diff-filter=ACMRD")
    return out.splitlines() if rc == 0 else []


def lane_refusal(lane, blockers):
    lines = ["", f"  REFUSED BY THE LANE GATE -- this commit says 'Lane: {lane}' and touches a file "
                 f"{lane} does not own.", ""]
    for path, owner, _pat, cur in blockers:
        why = []
        if owner and owner != lane:
            why.append(f"owned by {owner}")
        if cur and cur.get("lane") != lane:
            why.append(f"claimed by {cur['lane']} until {cur.get('until', '?')}")
        lines.append(f"    {path}" + (f"  -- {'; '.join(why)}" if why else ""))
    lines += ["",
              "  One file, one owner. If your change really belongs in that file, claim it first:",
              "",
              f"    python3 scripts/lanes.py claim {lane} <path> \"<why>\"",
              "",
              "  then re-commit, and release it once your change merges:",
              "",
              f"    python3 scripts/lanes.py release {lane} <path>",
              "",
              "  The map is lanes.<lane>.owns in projects/local-inference/models/ROLES.json"
              " (python3 scripts/role_store.py owns).",
              "  A claim lasts 4 h; nothing was committed.", ""]
    return "\n".join(lines)


def check_lane(msg_file):
    """Refuse a lane commit that touches a file another lane owns or has claimed (commit B, 2026-10-27).

    FOUR INSTANCES, ONE REPOSITORY. The lane prompts say "claim a file before editing it" and "YOUR
    LANE OWNS: taskstore.py, role_gate.py, ...", and until now both were sentences: a lane could
    commit into another lane's file and the only thing that noticed was a person reading the diff.
    This makes the claim real. It reads the LIVE board (lanes.BOARD, an absolute path outside git)
    rather than anything in a worktree, so a claim one lane makes is visible to the commit another
    lane is making in a different worktree.

    WHY HERE AND NOT pre-commit: the rule keys on the `Lane:` trailer, and git runs pre-commit
    BEFORE it assembles the message -- that hook cannot see it. commit-msg is the first hook that
    has both the message and `git diff --cached`, and it still refuses before any commit object
    exists. scripts/hooks/commit-msg calls this one line after validating the trailer's shape.

    FAILS OPEN, deliberately and narrowly: no Lane trailer (the services, Jas, every pre-2026-10-26
    model commit) means no gate; and an unreadable store or board means no gate, because a broken
    role store must not block the commit that repairs it -- role_store.check() and lanes.py report
    their own damage. The one thing it never does is guess which lane you are.
    """
    text = "\n".join(l for l in Path(msg_file).read_text(encoding="utf-8", errors="replace")
                     .splitlines() if not l.startswith("#"))
    m = LANE_RE.search(text)
    if not m:
        return 0
    lane = m.group(1)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import lanes as board
        import role_store
    except ImportError:
        return 0
    try:
        d = role_store.load()
        claims = board.active_claims()
    except (OSError, ValueError):
        return 0
    held = {(role_store.norm_path(w)): c for w, c in claims.items()}
    blockers = []
    for path in _staged_files():
        p = role_store.norm_path(path)
        owner, pat = role_store.owner_of(p, d)
        cur = held.get(p)
        if cur is not None and cur.get("lane") == lane:
            continue                                   # my claim: the borrow protocol
        if owner == lane:
            continue                                   # mine
        if owner is None and cur is None:
            continue                                   # nobody's
        blockers.append((p, owner, pat, cur))
    if not blockers:
        return 0
    print(lane_refusal(lane, blockers), file=sys.stderr)
    return 1


def model_of(agent):
    """"dsh-harness/deepseek-v4.1-flash" -> "deepseek-v4.1-flash". One model agreeing with itself is
    not the second signature the six guarded files need (AGENTS.md, "A note on agreement"), and that
    rule is about the MODEL: the tool before the "/" used to be compared instead, which made every
    ollama/* model one signer and one model under two harnesses two (Jas, 2026-10-27: "let's get rid
    of the word family entirely")."""
    a = (agent or "").strip()
    return a.split("/", 1)[1] if "/" in a else a


def guarded_touched(paths):
    """The guarded files in `paths`. A directory entry guards everything under it."""
    return [p for p in paths
            if any(p == q or (q.endswith("/") and p.startswith(q)) for q in GUARDED)]


def commits_brought(old, new):
    """The MODELS that wrote the commits `new` brings on top of `old`.

    The Agent trailer on a MERGE COMMIT names the MERGER, and the merger is not the author. Reading
    it as one made the gate refuse its own author's branch: L3 (claude-code) signing L2's
    DeepSeek-written branch was refused as "a reviewer in the AUTHOR'S OWN model (claude-code)",
    because both the trailer and the review said claude-code and the gate never looked at who wrote
    the work ([0c0d20], 2026-10-27). The commits are the work; running `git merge` is not.

    A commit with no Agent trailer contributes no model. If NOTHING here has a trailer the caller
    falls back to the message's own trailer, which is the strict reading rather than a free pass.
    """
    rc, out = git("log", "--no-merges", "--format=%B", f"{old}..{new}")
    if rc != 0:
        return set()
    return {model_of(m.group(1)) for m in AGENT_RE.finditer(out or "")}


def review_refusal(files, authors, reviews):
    lines = ["", "  REFUSED: this merge brings a two-signature file and no valid review of it.", ""]
    for f in files:
        lines.append(f"    {f}")
    lines.append("")
    wrote = ", ".join(sorted(a for a in authors if a)) or "(no Agent trailer on the commits)"
    lines.append(f"  The commits this merge brings were written by: {wrote}.")
    lines.append("")
    if not reviews:
        lines.append("  Add, above the Agent: trailer, one line naming who reviewed it and what they did:")
        lines.append("")
        lines.append("    Reviewed-by: <tool>/<model> (<scope>)")
        lines.append("")
        lines.append("  scope must be one of: diff (read the change), oracle (checked a test can fail),")
        lines.append("  suite-run (ran the suites), live (checked the running system).")
    else:
        lines.append("  Every review here is by a model that also WROTE it: "
                     + ", ".join(r[0] for r in reviews) + ".")
        lines.append("  A model agreeing with itself is not a second signature, whoever runs the merge.")
        lines.append("  Ask a different model to read the diff:")
        lines.append("    Reviewed-by: <tool>/<a model that did not write it>/<model> (<scope>)")
    lines += ["", "  The six files: " + ", ".join(GUARDED) + ".", "  Nothing was committed.", ""]
    return "\n".join(lines)


def review_verdict(text, files, authors=None):
    """(ok, why-not) for a message that must carry a review of `files`. One copy, two callers.

    `authors` are the models who wrote what the merge brings. The rule: EVERY author model must
    have a reviewer that is another model -- one model's signature covers another model's work, and
    nobody's covers their own. A branch that mixes two models therefore needs a review from each
    of them, which is the honest cost of mixing. No `authors` (an old branch with no trailers) falls
    back to the Agent line on the message.
    """
    authors = set(authors or ())
    if not authors:
        m = AGENT_RE.search(text)
        authors = {model_of(m.group(1))} if m else set()
    reviews = REVIEW_RE.findall(text)
    if authors and all(any(model_of(r) != a for r, _scope in reviews) for a in authors):
        return True, ""
    return False, review_refusal(files, authors, reviews)


def check_review(msg_file, text=None):
    """A merge into main that brings a guarded file needs a Reviewed-by from another model."""
    # WHAT THE BRANCH CHANGED, not how its tree differs from main's. `git diff HEAD MERGE_HEAD`
    # also lists a guarded file MAIN changed while the branch was waiting -- so a branch that touched
    # no guarded file at all was charged for another lane's, and a real finding sat among filenames
    # the author never opened (L4's refusal (2), 2026-10-27). The merge base is the fork point: the
    # diff from there is exactly the pair of trees the branch will contribute.
    rc, base = git("merge-base", "HEAD", "MERGE_HEAD")
    # NO MERGE BASE IS NOT "NOTHING TO CHECK". This returned 0, so a merge whose base git could not
    # find (unrelated histories, a shallow clone) brought guarded files with no signature at all --
    # the gate failed OPEN (L3's handover, 2026-10-27). Fall back to the whole tree difference: it can
    # only over-charge, never under-charge.
    since = base.strip() if rc == 0 and base.strip() else "HEAD"
    rc, out = git("diff", "--name-only", since, "MERGE_HEAD")
    files = guarded_touched(out.splitlines() if rc == 0 else [])
    if not files:
        return 0                                   # no tax on an ordinary merge
    if text is None:
        text = "\n".join(l for l in Path(msg_file).read_text(encoding="utf-8", errors="replace")
                          .splitlines() if not l.startswith("#"))
    # The AUTHOR range stays HEAD..MERGE_HEAD, deliberately: that is the commits main does not have,
    # i.e. the branch's own work. base..MERGE_HEAD would also sweep in other lanes' commits that the
    # branch merged from main, and charge their authors a review they do not owe here.
    ok, why = review_verdict(text, files, commits_brought("HEAD", "MERGE_HEAD"))
    if ok:
        return 0
    print(why, file=sys.stderr)
    return 1


def check_ref_update(lines):
    """reference-transaction 'prepared': refuse main moving onto a coder/* tip."""
    updates = [l.split() for l in lines if len(l.split()) == 3]
    target = [new for old, new, ref in updates if ref == f"refs/heads/{GATED_BRANCH}" and new != ZERO]
    if not target:
        return 0
    rc, out = git("for-each-ref", "--format=%(objectname) %(refname)", "refs/heads/coder/",
                  "refs/heads/bridge/")
    tips = {}
    for row in out.splitlines():
        sha, _, ref = row.partition(" ")
        tips.setdefault(sha, ref)
    for new in target:
        if new in tips:
            print(f"\n  REFUSED BY THE ROLE GATE (t476) -- {GATED_BRANCH} would move onto the tip of "
                  f"{tips[new][len('refs/heads/'):]}.\n  A build reaches {GATED_BRANCH} only as a "
                  "merge commit (git merge --no-ff) signed by a role\n  with merge_review; a "
                  "fast-forward has no signature to check.\n", file=sys.stderr)
            return 1
    # A FAST-FORWARD HAS NOWHERE TO PUT A SIGNATURE (2026-10-27, L2 Phase 0b). The merge-commit rule
    # is worth nothing if `git merge --ff-only` can bring the same guarded file in with no message at
    # all, which is exactly how the lanes merge today. Same reasoning as the coder/* rule above: a
    # fast-forward is refused, and the merge must be made with --no-ff and a Reviewed-by trailer.
    for old, new, ref in updates:
        if ref != f"refs/heads/{GATED_BRANCH}" or new == ZERO or old == ZERO:
            continue
        rc2, changed = git("diff", "--name-only", old, new)
        files = guarded_touched(changed.splitlines() if rc2 == 0 else [])
        if files:
            # A MERGE COMMIT IS NOT A FAST-FORWARD, and this hook sees both as "main moved". The new
            # commit's own message is where a signature lives, so read it rather than assuming
            # -- without this, the rule refused every signed merge as well as every fast-forward.
            parents = git("rev-list", "--parents", "-n1", new)[1].split()
            if len(parents) >= 3:
                ok, why = review_verdict(git("log", "-1", "--format=%B", new)[1], files,
                                         commits_brought(old, new))
                if ok:
                    continue
                print(why, file=sys.stderr)
                return 1
            # NOT A MERGE -- and two different things look identical to a hook that only sees old and
            # new: a FAST-FORWARD onto another branch's tip (no commit of its own, so nowhere to put
            # a signature) and a DIRECT COMMIT on main (a revert, an emergency fix -- it HAS a
            # message, so a signature can live in it). Telling them apart matters: on 2026-10-27 an
            # emergency revert of a guarded file was refused as "this fast-forward would change a
            # two-signature file", which is false and tells the reader to do something that does not
            # apply (L1's handover found it; I hit it again building a fixture). Any OTHER branch
            # pointing at the new commit is what makes it a fast-forward.
            _rc, at = git("for-each-ref", "--points-at", new, "--format=%(refname)", "refs/heads/")
            elsewhere = [r for r in at.splitlines() if r != f"refs/heads/{GATED_BRANCH}"]
            if elsewhere:
                print("\n  REFUSED: this fast-forward would change a two-signature file:\n    "
                      + "\n    ".join(files)
                      + f"\n\n  A fast-forward creates no commit, so there is nowhere to record who "
                        f"reviewed it.\n  Merge with --no-ff and a trailer naming a DIFFERENT model"
                        f":\n\n    git merge --no-ff <branch> -m \"<subject>\\n\\n"
                        f"Reviewed-by: <tool>/<another model> (diff|oracle|suite-run|live)\\n"
                        f"Agent: <you>\"\n"
                        f"\n  Ask the same question without moving anything, first:"
                        f"\n    python3 scripts/role_gate.py preflight <branch> <message-file>\n",
                      file=sys.stderr)
                return 1
            ok, why = review_verdict(git("log", "-1", "--format=%B", new)[1], files,
                                     commits_brought(old, new))
            if ok:
                continue
            print("\n  REFUSED: this DIRECT commit on " + GATED_BRANCH
                  + " changes a two-signature file:\n    " + "\n    ".join(files)
                  + "\n\n  It is not a fast-forward -- it has its own message, so a signature belongs"
                    "\n  in it, by a model that did not write it. This is also the emergency"
                    "\n  revert path: a revert that carries the second signature is allowed.\n\n"
                    "    Reviewed-by: <tool>/<another model> (diff|oracle|suite-run|live)\n\n"
                  + why, file=sys.stderr)
            return 1
    return 0


def preflight(branch, msg_file=None):
    """Decide whether a merge into main would be allowed, BEFORE git touches anything.

    reference-transaction runs on 'prepared', which is after git has already written the merge into
    the index and the working tree. L3's refusal on 2026-10-27 left 14 files staged under an
    unchanged HEAD, and a refusal that is not a no-op teaches lanes to force things. Git runs no hook
    earlier for a merge, so the only place to make the decision a no-op is before git is called --
    `scripts/merge_lock.py run` is where this belongs.
    """
    rc, old = git("rev-parse", f"refs/heads/{GATED_BRANCH}")
    old = old.strip()
    if rc != 0 or not old:
        print(f"  cannot read refs/heads/{GATED_BRANCH}", file=sys.stderr)
        return 2
    rc, base = git("merge-base", old, branch)
    if rc != 0:
        print(f"  cannot find a merge base between {GATED_BRANCH} and {branch}", file=sys.stderr)
        return 2
    files = guarded_touched(git("diff", "--name-only", base.strip(), branch)[1].splitlines())
    if not files:
        print(f"  OK: {branch} touches none of the six two-signature files -- no review needed.")
        return 0
    text = ""
    if msg_file:
        text = "\n".join(l for l in Path(msg_file).read_text(encoding="utf-8", errors="replace")
                         .splitlines() if not l.startswith("#"))
    ok, why = review_verdict(text, files, commits_brought(old, branch))
    if ok:
        print("  OK: " + branch + " brings " + ", ".join(files)
              + " and carries a review from a model that did not write it.")
        return 0
    print(why, file=sys.stderr)
    return 1


def main(argv):
    if len(argv) >= 3 and argv[1] == "commit-msg":
        rc = check_commit(argv[2])
        if rc:
            return rc
        return check_review(argv[2]) if task_for_merge() is not None else 0
    if len(argv) >= 3 and argv[1] == "preflight":
        return preflight(argv[2], argv[3] if len(argv) > 3 else None)
    if len(argv) >= 3 and argv[1] == "lane-owns":
        return check_lane(argv[2])
    if len(argv) >= 2 and argv[1] == "ref-update":
        return check_ref_update(sys.stdin.read().splitlines())
    if len(argv) >= 3 and argv[1] == "who":
        roles = load_roles()
        key = role_for(argv[2], roles)
        print(f"{argv[2]} -> {key or '(no role)'}"
              + (f"; may merge: {has_task(roles[key], MERGE_TASK)}; may commit directly to "
                 f"{GATED_BRANCH}: {has_task(roles[key], DIRECT_TASK)}; may control units (systemctl, from the Merge review "
                 f"responsibility on the Models tab): "
                 f"{has_task(roles[key], MERGE_TASK)}" if key else ""))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
