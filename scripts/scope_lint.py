#!/usr/bin/env python3
"""Mechanical checks on a manager answer's chunks block, BEFORE the bridge -- the upstream fixture.

    python3 scripts/scope_lint.py <answer.md> --task tNNN [--record] [--no-run]

Exit 0 = every check passed; 2 = findings (printed, one per line, with their class); 1 = could
not lint (no block). --record writes each check's outcome to scope_quality, against the model that
wrote the answer. --no-run skips executing oracles (the static checks only).

Jas, 2026-09-28: "We need a mechanical fixture upstream to increase quality of scopes to ensure
more oracles and briefs are error free ... All of this needs to address and mechanically fix
upstream processes." And from then on Claude does not move oracles across the bridge: this, the
bridge's own validation and the builder's preflight are what stand in its place.

WHAT IS CHECKED, and the class a failure is recorded under (scope_quality.SCOPE_CLASSES):
  chunks-invalid   bridge.parse / bridge.validate refuse the block (one file per chunk, target
                   exists or is an oracle in the block, done_when names the target, ...)
  oracle-no-runner the oracle defines tests but has no __main__ block, so running it runs nothing
                   (pytest is not installed; the builder runs `python3 <oracle>`)
  oracle-gate      the oracle's code breaks a live-state rule: a fixed /tmp path, a call with no
                   timeout, a model call, ps parsing. (library_checks exempts tests/ from its
                   rules; an oracle is run unattended by the builder, so these rules apply to it.)
  oracle-green     run on main in a throwaway worktree, the oracle PASSES: it proves nothing
  oracle-crash     it cannot run for a reason that is not the missing change: a syntax error
                   in the oracle itself, or no result inside ORACLE_TIMEOUT_S
  oracle-dirty     running it changed the worktree: it wrote outside its temp dirs
  brief-anchor     a SYMBOLS name the brief lists is not defined in the file, or a :LINE it cites
                   is past the end of the file
  oracle-red-reason red, but for its own bug: a module that exists cannot be imported, pytest, a
                   root one level short, a NotImplementedError placeholder, a signature the brief
                   never changes (calibration 2026-09-28: 14 of 64)
  brief-unresolved the text the builder receives carries a placeholder (FILL #1, "confirm before
                   filing", "[unverified" in SYMBOLS)
  brief-overlap    two implementation chunks edit one file: built separately, they conflict
  brief-window     the file is over WINDOW_BYTES and the brief names no SYMBOLS, so the whole
                   file goes into the builder's window (t447 and p20's window-full)
  brief-scatter    one chunk names more than MAX_EDIT_SITES separate edit sites in one file
                   (SYMBOLS plus cited lines): the builder's reply carries every edit and runs
                   into its output cap (t450: 11 sites in a 1639-line file, two attempts cut off
                   at 16000 tokens). Split it into chunks of at most MAX_EDIT_SITES
  brief-steps      (Jas, 2026-10-21) a step's `now` is not the file's text at its `where`, a `where`
                   names a symbol off the SYMBOLS line, or a chunk has more than MAX_EDIT_SITES
                   steps; with --require-steps (scope_gate passes it) a chunk with no steps at all
Every check that grows here comes from an RCA whose fix was "a check could have caught this".
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

ORACLE_TIMEOUT_S = 300
MAX_EDIT_SITES = 6             # t450: 11 scattered edits overflowed the 16k output cap twice
WINDOW_BYTES = 40_000          # the manager prompt's own rule: "a file over ~40 KB gets a SYMBOLS: line"
STANDING_RULE_FILES = ("docs/PREFERENCES.md", "docs/DECISIONS.md", "docs/LESSONS.md")  # t768: permanent-rule docs must be registered
SKILL_TARGET = re.compile(r"^docs/skills/[^/]+/SKILL[.]md$")
LIVE_STATE_CHECKS = ("fixed-tmp-path", "no-timeout", "model-call-outside-contract", "ps-command-column")
# t478: a refusal is a diagnosis with no prescription unless it names the repair. One sentence per
# live-state rule, appended to the finding's evidence so the author knows what to write. The
# sentences deliberately avoid the forbidden shapes themselves (this file is checked by
# scripts/library_checks.py too).
LIVE_STATE_REPAIRS = {
    "no-timeout": "put timeout=60 on every subprocess run/check_output/urlopen call "
                  "(the call blocks forever if the peer stalls)",
    "fixed-tmp-path": "build the path under tempfile.mkdtemp() instead of a fixed path",
    "model-call-outside-contract": "an oracle must not call a model endpoint, use a fake transport",
    "ps-command-column": "match a process by the full command column, not a name",
}

def skill_evidence_findings(chunks, repo=REPO):
    """brief-skill-evidence (t1091, RCA ac7ac8636e39): a dispatch that edits a SKILL.md must carry observed_failure_evidence, the dispatch/task id and the one-line observed failure that justifies the edit; a skill edit without one is a speculative improvement, and three passed (a4eaa838, 6b93b546, 8d3fd873)."""
    out = []
    for i, c in enumerate(chunks):
        if c.get("kind") != "implementation":
            continue
        f = str(c.get("file") or "").replace(chr(92), "/").strip()
        if f.startswith("./"):
            f = f[2:]
        if not SKILL_TARGET.match(f):
            continue
        ev = c.get("observed_failure_evidence")
        if not isinstance(ev, str) or not ev.strip():
            out.append(("brief-skill-evidence", i, f,
                        "an implementation chunk edits a skill file with no observed_failure_evidence: add it as "
                        "'<dispatch/task id>: <one-line observed failure>' -- a skill-file edit without an observed "
                        "failure is a speculative improvement (t1091)"))
    return out
# ORACLES MAY NOT READ LIVE STATE (2026-10-14). Three suites went red on main that day because they
# read the live role store, the live dispatch ledger or the live Hub board -- a Models-tab edit or a new
# ledger row turned them red, and a suite red on main holds every build on the line. A test needs a
# fixture it owns; naming a live source with no temp dir or copy is refused back to the scoper.
LIVE_STATE = re.compile(r"run-logs/|auto-assigned\.json|dispatch-loop\.jsonl|agent-runs\.jsonl|ROLES\.json"
                        r"|\bboard_page\(|\b[A-Za-z_]+\.ROUTING\b|task_routing\.load\(\)")
OWN_FIXTURE = re.compile(r"tempfile|mkdtemp|TemporaryDirectory|deepcopy\(")


def live_state_hits(text):
    """The live sources a test names, when it builds no fixture of its own; [] otherwise."""
    if OWN_FIXTURE.search(text or ""):
        return []
    return sorted(set(LIVE_STATE.findall(text or "")))


def live_state_findings(chunks, repo=REPO):
    """oracle-live-state: an oracle chunk whose text reads live repository state with no fixture."""
    out = []
    for i, c in enumerate(chunks):
        if c.get("kind") != "oracle":
            continue
        hits = live_state_hits(str(c.get("oracle_text") or ""))
        if hits:
            out.append(("oracle-live-state", i, str(c.get("file") or ""),
                        "the oracle reads live state (" + ", ".join(hits) + ") with no fixture of its own: a "
                        "Models-tab edit or a new ledger row would turn it red on main and hold every build. "
                        "Copy what it needs into tempfile.mkdtemp() (or deepcopy the routing) and point the "
                        "module at the copy"))
    return out


CROSS_COMPONENT_CLASS = 'cross-component-contract'
IGNORED_DIRT = ("scripts/library.json", "workspace/SCRIPTS.md")   # registering the oracle writes these


def _git(repo, *args, timeout=120):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)


def _never_fails(src):
    """True when an oracle's code has no way to fail but crashing: no assert, no raise, no exit call
    and no self.assert*/assert_* helper. Such an oracle pins no behaviour -- t471/t297/t370 asserted
    shape and passed; the extreme case asserts nothing. Unparseable code is oracle-crash's, not ours."""
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assert, ast.Raise)):
            return False
        if isinstance(n, ast.Call):
            fn = n.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if name in ("exit", "_exit") or name.startswith("assert"):
                return False
    return True


def _in_test_scope(tree):
    """Return ids of nodes whose failure-raising syntax can run under the test runner.

    Helper bodies that the suite's runner never calls pin no behaviour: a raise
    there is a token, not a test.
    """
    import ast

    pinned = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                pinned.update(id(n) for n in ast.walk(node))
            else:
                pinned.update(id(d) for d in node.decorator_list)
                pinned.update(id(d) for d in node.args.defaults)
                pinned.update(id(d) for d in node.args.kw_defaults if d is not None)
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name.startswith("test_"):
                    pinned.update(id(n) for n in ast.walk(member))
        else:
            pinned.update(id(n) for n in ast.walk(node))
    return pinned


def _shape_only(src):
    import ast
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    pinned = _in_test_scope(tree)
    has_assert = False
    has_bare_assert_called = False
    for n in ast.walk(tree):
        if isinstance(n, ast.Raise):
            if id(n) in pinned:
                return False
        if isinstance(n, ast.Call):
            fn = n.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if name in ("exit", "_exit", "fail"):
                if id(n) in pinned:
                    return False
            if name.startswith("assert"):
                if name == "assert_called" and not n.args and not n.keywords:
                    if id(n) in pinned:
                        has_bare_assert_called = True
                elif id(n) in pinned:
                    return False
        if isinstance(n, ast.Assert):
            has_assert = True
            t = n.test
            if isinstance(t, ast.Call) and isinstance(t.func, ast.Name) and t.func.id == "isinstance":
                continue
            if isinstance(t, ast.Compare) and len(t.ops) == 1 and len(t.comparators) == 1:
                op = t.ops[0]
                r = t.comparators[0]
                if isinstance(op, ast.In) and isinstance(t.left, ast.Constant) and isinstance(t.left.value, str):
                    continue
                if isinstance(op, ast.Eq):
                    is_len = lambda node: isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len"
                    is_num = lambda node: isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool)
                    is_cont = lambda node: isinstance(node, ast.Name) and node.id in {"list", "dict", "set", "tuple", "str", "int", "float", "bool"}
                    if (is_len(t.left) and is_num(r)) or (is_len(r) and is_num(t.left)) or is_cont(t.left) or is_cont(r):
                        continue
            return False
    return True if has_assert else has_bare_assert_called



def standing_rule_findings(chunks, repo):
    """brief-anchor: an edit to a standing-rule doc must name the FLOW.json node it registers to (t768)."""
    out = []
    chart = Path(repo) / "projects" / "local-inference" / "models" / "FLOW.json"
    ids = set()
    chart_problem = ""
    try:
        data = json.loads(chart.read_text(encoding="utf-8"))
        ids = {t.get("id") for t in data.get("tasks", []) if isinstance(t, dict) and t.get("id")}
    except Exception as exc:
        # A missing chart is not a licence to stay silent: record why it is unreadable (t768).
        chart_problem = f"projects/local-inference/models/FLOW.json is unreadable ({type(exc).__name__})"
    for i, c in enumerate(chunks):
        f = str(c.get("file") or "").replace("\\", "/").strip()
        if f.startswith("./"):
            f = f[2:]
        f = f.strip()
        if f not in STANDING_RULE_FILES:
            continue
        nid = c.get("flow")
        if not isinstance(nid, str) or not nid.strip():
            prose = " ".join(str(c.get(k) or "") for k in ("single_change", "what_is_wrong", "behaviour_oracle"))
            m = re.search(r"flow\s*[:=]\s*(\S+)", prose, re.I)
            nid = m.group(1) if m else ""
        nid = str(nid).strip()
        if chart_problem:
            out.append(("brief-anchor", i, f, f"the brief edits {f}, a standing-rule file, while its "
                        f"{chart_problem}; add a top-level 'flow' naming a FLOW.json node"))
            continue
        if not nid:
            out.append(("brief-anchor", i, f, f"the brief edits {f}, a standing-rule file, with no flow node: "
                        "a standing-rule target must name a FLOW.json node"))
        elif nid not in ids:
            out.append(("brief-anchor", i, f, f"flow id {nid!r} is not a task node in "
                        "projects/local-inference/models/FLOW.json; register the change in the chart"))
    return out


def contract_violation(row):
    """Return a one-line reason why `row` breaks the cross-component contract vocabulary.

    The row may be a defect row, a failure-schema row, or a brief chunk; callers opt in by asking,
    so a well-formed implementation chunk with no 'contract' key is not flagged by scope_lint.
    """
    if "contract" not in row:
        return "names no contract: a cross-component defect must state the producer->consumer contract it lives on"
    contract = row["contract"]
    if isinstance(contract, dict):
        for key in ("producer", "consumer", "invariant"):
            value = contract.get(key)
            if not isinstance(value, str) or not value.strip():
                return f"contract is missing a non-empty string {key!r}"
        if contract["producer"].strip() == contract["consumer"].strip():
            return "contract must name two distinct ends: producer and consumer are the same"
        return ""
    if isinstance(contract, str):
        if contract.strip().lower() in ("none", "n/a"):
            if row.get("class") == CROSS_COMPONENT_CLASS:
                return "a 'none' contract is not allowed on a cross-component row: name the two ends"
            reason = row.get("contract_reason")
            if not isinstance(reason, str) or not reason.strip():
                return "a 'none' contract requires a non-empty contract_reason"
            return ""
        return f"contract must be a producer/consumer/invariant dict, not a string: {contract!r}"
    return f"contract must be a dict (or a legal 'none' string), not {type(contract).__name__}"

def syms_present(c):
    """True when the chunk names at least one symbol."""
    return any(isinstance(s, str) and s.strip() for s in (c.get("symbols") or []))


def edit_sites(c, f, text, syms):
    """How many places the builder must edit: each named SYMBOL, plus each line cited in `single_change` that falls
    OUTSIDE every named symbol.

    A CITATION IS NOT AN EDIT SITE (B5, 2026-10-30). This counted SYMBOLS plus every `file:LINE` in
    single_change, what_is_wrong AND behaviour_oracle, while manager_prompt.py tells the scoper to "cite file:LINE
    from the numbered reads" as evidence. Measured over every scoper answer on disk: 366 chunks were over 6, and
    283 of them (77%) named 6 or fewer SYMBOLS -- pushed over by evidence citations, and by a line inside a named
    symbol being counted a second time (127 chunks). The scoper was refused for obeying its own prompt."""
    names = {s.strip().split(' ')[0].split('(')[0] for s in syms}
    cited = set()
    for m in re.finditer(rf"(?<![\w.]){re.escape(Path(f).name)}((?::\d{{1,5}}(?:-\d{{1,5}})?(?:/)?)+)",
                         str(c.get("single_change") or "")):
        cited |= {int(x) for x in re.findall(r"\d{1,5}", m.group(1))}
    spans = []
    if cited and names and f.endswith(".py") and text:
        import ast
        bare = {n.split('@')[0].split('.')[-1] for n in names}
        try:
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in bare:
                    spans.append((node.lineno, node.end_lineno or node.lineno))
        except SyntaxError:
            spans = []                                  # unparseable: every citation counts, as before
    return len(names) + len({ln for ln in cited if not any(a <= ln <= b for a, b in spans)})


def static(chunks, repo):
    """[(class, chunk index, file, evidence)] from the checks that need no execution."""
    import library_checks
    out = []
    seen_files, seen_targets = {}, {}
    for i, c in enumerate(chunks):
        text_all = json.dumps({k: v for k, v in c.items() if k != "red_because"})
        m = PLACEHOLDER.search(text_all)
        if m or any("[unverified" in str(s_).lower() for s_ in (c.get("symbols") or [])):
            out.append(("brief-unresolved", i, c.get("file") or "",
                        f"carries a placeholder ({m.group(0) if m else '[unverified in SYMBOLS'}) -- the "
                        f"builder does not troubleshoot; resolve it or answer NEEDS JAS / BLOCKED"))
        if c.get("kind") == "implementation" and c.get("file"):
            if c["file"] in seen_files:
                out.append(("brief-overlap", i, c["file"], f"chunk {seen_files[c['file']]} also edits "
                            f"{c['file']}: built separately from main they conflict -- make it one chunk"))
            seen_files.setdefault(c["file"], i)
            # ONE IMPLEMENTATION CHUNK PER ORACLE (t458, 2026-10-02). Each chunk is built ALONE and
            # must turn its own target green; two on one target mean neither can (p238: a JSON chunk
            # on a scope_lint oracle, refused by preflight).
            t = c.get("target")
            if t and t in seen_targets:
                out.append(("brief-shared-target", i, c["file"], f"chunk {seen_targets[t]} has the same "
                            f"target {t}: built alone, neither can turn it green -- give each its own oracle"))
            seen_targets.setdefault(t, i)
        f = c.get("file") or ""
        if c.get("kind") == "oracle":
            probe = f.replace("scripts/tests/", "scripts/")      # outside tests/, so the rules apply
            ot = c.get("oracle_text") or ""
            # A NON-PARSING ORACLE IS A PROPERTY OF ITS TEXT (t686): oracle-crash was produced only by
            # run_oracles, which executes it in a worktree, so --no-run never saw the t669 shape
            # (2026-10-05: "':' expected after dictionary key"). Decided here without running anything.
            import ast
            try:
                ast.parse(ot)
            except SyntaxError as e:
                out.append(("oracle-crash", i, f, f"the oracle itself does not parse (static): line "
                            f"{e.lineno}: {e.msg} -- decided without executing, so it is available "
                            "under --no-run"))
            for cid, _sev, line, code in library_checks.run(probe, ot, only=LIVE_STATE_CHECKS):
                repair = LIVE_STATE_REPAIRS.get(cid, "")
                evidence = f"{cid} at line {line}: {code}"
                if repair:
                    evidence += f" -- repair: {repair}"
                out.append(("oracle-gate", i, f, evidence))
            # NO RUNNER, NO TEST (calibration 2026-09-28: 10 of 75 Q4 oracles). The builder runs
            # `python3 <oracle>` and pytest is not installed, so test functions with no __main__
            # block execute nothing: red only while an import fails, green for an empty stub.
            if re.search(r"^\s*(def test_|class Test)", ot, re.M) and not re.search(
                    r"^if __name__ == [\"']__main__[\"']", ot, re.M):
                out.append(("oracle-no-runner", i, f, "defines tests but has no `if __name__ == "
                            "\"__main__\":` block, so `python3 " + f + "` runs none of them"))
            # ORACLE-WEAK (t502): an oracle that cannot fail except by crashing proves nothing.
            if ot.strip() and (_never_fails(ot) or _shape_only(ot)):
                out.append(("oracle-weak", i, f, "no assert, raise or exit call (or only shape/presence "
                            "assertions) -- it can only fail by crashing, so it pins no behaviour; "
                            "assert the value is consumed, the real constants, the request body's "
                            "required fields"))
        elif c.get("kind") == "implementation":
            p = repo / f
            text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
            # A SEAM NEEDS TWO ENDS (t789): a defect true only across components is not statable as
            # a single-function row, so opt-in rows that declare a contract must name it.
            msg = contract_violation(c) if "contract" in c else ""
            if msg:
                out.append(("contract-invalid", i, f, msg))
            # THE BUILDER EDITS; IT CANNOT CREATE (2026-09-30). coder.py's apply_edits refuses a path
            # that is not a file ("no such file"), and preflight refuses the brief in 0 s for the
            # same reason. p154 (t124, an HTML page) and p156 were scoped as "add exactly one
            # file", enrolled, and refused at every tick -- 62 times for p154. Caught here, before
            # enrolment, the scope goes back to the Manager with the reason instead.
            # THE BRIDGE DECLARES ORACLES ITSELF (bridge.py, at freeze): an oracle chunk is written
            # declared red in the failure ceiling in the same commit. A chunk that asks the builder
            # to make that declaration duplicates it, and its target -- the oracle -- is red BY
            # DESIGN, so no build can pass it (p158/t267, t462: three attempts, parked).
            if f in ("workspace/test-failures-ceiling.json", "workspace/sandbox-baseline.json"):
                out.append(("brief-unsatisfiable", i, f,
                            f"{f} is not the builder's to edit: the bridge declares every oracle in the "
                            "block red there when it freezes it. Drop this chunk"))
                continue
            if not p.is_file():
                out.append(("brief-unsatisfiable", i, f,
                            f"{f} does not exist, and the builder (coder.py) can only edit a file that exists "
                            "-- it cannot create one. Put the change into an existing file, or assign the "
                            "task to Claude to create the file first"))
            syms = [s for s in (c.get("symbols") or []) if isinstance(s, str) and s.strip()]
            # SYMBOLS IS REQUIRED ON A PYTHON CHUNK (t1072; Part B, 2026-10-19): auto_merge judges a
            # change narrow only against the symbols the brief names, so a brief naming none is work
            # that can never auto-merge -- 11 delivered builds sat with Claude for exactly this.
            # "append": true is the exemption the brief format already has (the tail is shown).
            if f.endswith(".py") and not syms_present(c) and not c.get("append"):
                out.append(("brief-no-symbols", i, f, f"names no SYMBOLS for {f}: list every function, "
                            f"class or CONSTANT the chunk changes, and the ones it creates under \"new\" "
                            f"-- without them the build can never auto-merge"))
            created = {str(n).strip() for n in (c.get("new") or []) if isinstance(n, str)}
            for s in syms:
                if re.search(r'\(\s*new\b', s, re.I) or s.strip() in created:
                    continue
                if not re.match(r'^[A-Za-z_][A-Za-z0-9_.]*(?:@\d+)?$', s.strip()):
                    out.append(("brief-anchor", i, f, f"SYMBOLS entry {s!r} is prose, not a bare name"))
                    continue
                name = s.strip().split(' ')[0].split('(')[0].split('@')[0].split('.')[-1]
                if not re.search(rf"^\s*(async\s+def|def|class)\s+{re.escape(name)}\b|^\s*{re.escape(name)}\s*[:=]",
                                 text, re.M):
                    out.append(("brief-anchor", i, f, f"SYMBOLS names {s!r}, which is not defined in {f}"
                                                      + ("" if text else " (the file does not exist)")))
            n = text.count("\n") + 1 if text else 0
            prose = " ".join(str(c.get(k) or "") for k in ("single_change", "what_is_wrong", "behaviour_oracle"))
            # Only citations of THIS file: `coder.py:111`, `coder.py:111-140`, `coder.py:111/:144`.
            # A bare ":4000" is as likely a port as a line (calibration false positive, t286).
            cited = set()
            for m in re.finditer(rf"(?<![\w.]){re.escape(Path(f).name)}((?::\d{{1,5}}(?:-\d{{1,5}})?(?:/)?)+)", prose):
                cited |= {int(x) for x in re.findall(r"\d{1,5}", m.group(1))}
            for ln in sorted(cited):
                if text and ln > n:
                    out.append(("brief-anchor", i, f, f"cites {Path(f).name}:{ln} but {f} has {n} lines"))
            sites = edit_sites(c, f, text, syms)
            if sites > MAX_EDIT_SITES:
                out.append(("brief-scatter", i, f, f"names {sites} separate edit sites in {f} (SYMBOLS + lines "
                                                   f"cited in single_change outside them); more than "
                                                   f"{MAX_EDIT_SITES} overflows the builder's "
                                                   f"output cap (t450). Split it into chunks of at most "
                                                   f"{MAX_EDIT_SITES} sites each"))
            appends = bool(c.get("append")) and not f.endswith(".py")      # t672: the tail is shown, not the file
            if p.is_file() and p.stat().st_size > WINDOW_BYTES and not syms and not appends:
                out.append(("brief-window", i, f, f"{f} is {p.stat().st_size} bytes (> {WINDOW_BYTES}) "
                                                  f"and the brief names no SYMBOLS" + ("" if f.endswith(".py") else
                                                  " (a non-Python file edited only at its end: set "
                                                  "\"append\": true)")))
    out.extend(standing_rule_findings(chunks, repo))
    out.extend(skill_evidence_findings(chunks, repo))
    out.extend(live_state_findings(chunks, repo))
    return out


def _run_in_group(cmd, cwd):
    """Run an oracle in its own process group and kill the group afterwards: an oracle that starts a
    fake server must not leave it running on the box (calibration 2026-09-28, t415's oracle)."""
    import signal
    p = subprocess.Popen(cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         stdin=subprocess.DEVNULL, start_new_session=True,
                         # PLAIN TRACEBACKS (2026-10-03): Python 3.14 colours them whenever FORCE_COLOR
                         # is set (Claude Code sets it), and the SyntaxError/NotImplementedError regexes
                         # below never matched an ANSI-wrapped name. PYTHON_COLORS=0 overrides it.
                         env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHON_COLORS": "0"})
    try:
        out, err = p.communicate(timeout=ORACLE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        p.communicate(timeout=30)
        raise
    finally:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            leftover = False      # the normal case: nothing in the group outlived the oracle
        else:
            leftover = True
    if leftover:
        print(f"scope_lint: killed processes the oracle left running (group {p.pid})", file=sys.stderr)
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def dirty_leaks_to_tracked_dir(dirty_line, tracked_paths):
    """True for a NEW untracked file ('??') written into a directory that already holds a tracked file.
    That is the live-state leak (t699; t659 wrote finding JSONs into workspace/session-reviews/findings/):
    an oracle must write under a temp dir, not into a tracked data directory. A modified tracked file or a
    new file in an untracked-only directory is not this shape."""
    line = dirty_line.strip()
    if line[:2] != "??" or len(line) < 4:
        return False
    parent = Path(line[3:]).parent
    return any(t and Path(t).parent == parent for t in tracked_paths)


def _repo_modules(repo):
    """Module names that exist in the repository (scripts/ and projects/*/, not tests)."""
    names = set()
    for d in [repo / "scripts", *sorted((repo / "projects").glob("*"))]:
        if d.is_dir():
            names |= {q.stem for q in d.glob("*.py")}
    return names


def wrong_reason(output, chunks, repo):
    """Why a red oracle is red for its OWN bug rather than the missing change, or ''.

    Calibration 2026-09-28: 14 of 64 Q4 scopes were red for a reason that could never turn green --
    a repo root one level short, pytest (not installed), a signature the brief never changes, a
    NotImplementedError placeholder. The oracle's failure is read, not just its exit code."""
    impl = " ".join(json.dumps(c) for c in chunks if c.get("kind") == "implementation")
    new_files = {Path(c.get("file") or "").stem for c in chunks if c.get("kind") == "implementation"
                 and not (repo / (c.get("file") or "")).exists()}
    m = re.search(r"No module named '([\w.]+)'", output)
    if m:
        mod = m.group(1).split(".")[0]
        if mod == "pytest":
            return "imports pytest, which is not installed here: the builder runs `python3 <oracle>`"
        if mod in _repo_modules(repo) and mod not in new_files:
            return f"cannot import {mod!r}, which exists in the repository: the oracle's sys.path is wrong"
    m = re.search(r"No such file or directory: '([^']+)'", output)
    if m and re.search(r"/scripts/(scripts|projects)/|/tests/(scripts|projects)/", m.group(1)):
        return f"reads {m.group(1)}: the repository root is computed one level short"
    if re.search(r"^NotImplementedError", output, re.M):
        return "raises NotImplementedError: a placeholder, not a test"
    m = re.search(r"(\w+)\(\) (?:got an unexpected keyword argument '(\w+)'|missing \d+ required positional "
                  r"arguments?: '(\w+)')", output)
    if m:
        arg = m.group(2) or m.group(3)
        if arg not in impl:
            return (f"calls {m.group(1)}() with a signature it does not have ({arg!r}) and the brief "
                    f"never changes that signature")
    return ""


PLACEHOLDER = re.compile(r"FILL #\d|\bresolve (?:it )?before dispatch|before filing|Claude, grep|manager-flag|"
                         r"adjust (?:this helper|_\w+\(\)) at filing", re.I)


def run_oracles(chunks, repo, ref="main"):
    """[(class, i, file, evidence)] from running each oracle on `ref` in a throwaway worktree."""
    oracles = [(i, c) for i, c in enumerate(chunks) if c.get("kind") == "oracle"]
    if not oracles:
        return []
    out = []
    tmp = Path(tempfile.mkdtemp(prefix="scope-lint-"))
    wt = tmp / "wt"
    try:
        made = _git(repo, "worktree", "add", "-q", "--detach", str(wt), ref)
        if made.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {made.stderr.strip()}")
        tracked = set(_git(wt, "ls-files").stdout.splitlines())
        for i, c in oracles:
            f = c["file"]
            (wt / f).parent.mkdir(parents=True, exist_ok=True)
            (wt / f).write_text(c["oracle_text"], encoding="utf-8")
            if (wt / "scripts" / "library.py").is_file():
                subprocess.run([sys.executable, str(wt / "scripts" / "library.py"), "register", f,
                                "scope_lint probe"], cwd=str(wt), capture_output=True, text=True, timeout=120)
            before = set(_git(wt, "status", "--porcelain", "--untracked-files=all").stdout.splitlines())
            try:
                r = _run_in_group([sys.executable, "-s", f], wt)
                tail = (r.stdout + r.stderr).strip().splitlines()[-6:]
                if r.returncode == 0:
                    out.append(("oracle-green", i, f, "exit 0 on main -- the oracle passes without the "
                                                      "change: " + " | ".join(tail)))
                elif re.search(rf'File "[^"]*{re.escape(Path(f).name)}", line \d+.*\n(?:.*\n)*?\s*(SyntaxError|IndentationError)',
                               r.stderr) or re.search(r"^(SyntaxError|IndentationError)", r.stderr, re.M):
                    out.append(("oracle-crash", i, f, "the oracle itself does not parse: " + " | ".join(tail)))
                else:
                    why = wrong_reason(r.stdout + "\n" + r.stderr, chunks, repo)
                    if why:
                        out.append(("oracle-red-reason", i, f, why + " -- " + " | ".join(tail)))
            except subprocess.TimeoutExpired:
                out.append(("oracle-crash", i, f, f"no result in {ORACLE_TIMEOUT_S}s"))
            after = set(_git(wt, "status", "--porcelain", "--untracked-files=all").stdout.splitlines())
            dirt = sorted(line[3:] for line in after - before
                          if line[3:].strip() not in IGNORED_DIRT and line[3:].strip() != f
                          and "__pycache__" not in line)
            if dirt:
                out.append(("oracle-dirty", i, f, "running it changed: " + ", ".join(dirt)))
                for line in sorted(after - before):
                    if line[3:].strip() in dirt and dirty_leaks_to_tracked_dir(line, tracked):
                        out.append(("dirty-leaks-to-tracked-dir", i, f, "oracle wrote a new file into a "
                                    "tracked data dir (live-state leak): " + line[3:]))
    finally:
        _git(repo, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def unanchored(chunks, repo):
    """brief-unanchored: an implementation chunk that edits a file with NOTHING to anchor the edit
    to -- no line of that file cited, no SYMBOLS entry, no append:true -- is t502's brief-wrong
    shape: the builder has no window, re-emits the file from guess, and burns 5.7-24k tokens per
    attempt. Every existing anchor check fires on an anchor that WAS cited; this one fires on its
    absence."""
    out = []
    for i, ch in enumerate(chunks):
        if ch.get("kind") != "implementation":
            continue
        if [s for s in (ch.get("symbols") or []) if str(s).strip()]:
            continue
        if str(ch.get("append", "")).lower() in ("true", "yes", "1"):
            continue
        f = str(ch.get("file", "")).strip()
        p = Path(repo) / f
        if not p.is_file():
            continue
        n = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        prose = " ".join(str(ch.get(k) or "") for k in
                         ("single_change", "what_is_wrong", "behaviour_oracle"))
        # The SAME citation pattern brief-anchor uses (scripts/scope_lint.py:222): only `name.py:N`,
        # `name.py:N-M` or `name.py:N/:M` count. A bare number in the prose is not a line citation.
        hits = set()
        for m in re.finditer(rf"(?<![\w.]){re.escape(Path(f).name)}((?::\d{{1,5}}(?:-\d{{1,5}})?(?:/)?)+)", prose):
            hits |= {int(x) for x in re.findall(r"\d{1,5}", m.group(1))}
        if any(1 <= k <= n for k in hits):
            continue
        out.append(("brief-unanchored", i, f,
                    "the brief edits this file with no line in it, no SYMBOLS entry and no "
                    "append:true, so the builder has no window to anchor the edit to and must "
                    "guess the file's structure (t502: five attempts, 5.7-24k tokens each, "
                    "syntax errors in a re-emitted file)"))
    return out


_LINE_RANGE = re.compile(r"^L?(\d{1,6})(?:\s*-\s*L?(\d{1,6}))?$")
STEP_LINE_SLACK = 2            # a cited range may be off by a line or two; the text is what is checked


def _norm_lines(text):
    """Lines with whitespace collapsed and blank lines dropped: what `now` is compared on."""
    return [" ".join(ln.split()) for ln in str(text).splitlines() if ln.strip()]


def _holds(hay, needle):
    n = len(needle)
    return n > 0 and any(hay[k:k + n] == needle for k in range(len(hay) - n + 1))


def steps_findings(chunks, repo, require=False):
    """brief-steps: each step's `now` must be the file's text at its `where` -- the one mechanical proof
    that the scoper read the file it scopes (Jas, 2026-10-21: scopes as granular as a plan). `where` is
    a SYMBOLS name or L<start>-L<end>. With require, an implementation chunk with no steps is a finding;
    without it (every older caller) it is not -- live scopers get the requirement from scope_gate."""
    import auto_merge
    out = []
    for i, c in enumerate(chunks):
        if c.get("kind") != "implementation":
            continue
        f = str(c.get("file") or "")
        steps = c.get("steps")
        if not steps:
            if require:
                out.append(("brief-steps", i, f,
                            f"no steps: give the chunk a \"steps\" list of 1 to {MAX_EDIT_SITES} "
                            f"{{where, now, do, expect, if_not}} -- where is a SYMBOLS name or L<start>-L<end>, "
                            f"now is the file's text there TODAY copied from read_file, do is the change, expect "
                            f"is what is true after it (the last one is done_when), if_not is what the builder "
                            f"does when it is not"))
            continue
        if not isinstance(steps, list):
            continue                          # bridge.validate already refused the shape
        if len(steps) > MAX_EDIT_SITES:
            out.append(("brief-steps", i, f, f"{len(steps)} steps; at most {MAX_EDIT_SITES} per chunk "
                                             f"-- split it into chunks (t450)"))
        p = Path(repo) / f
        if not p.is_file():
            continue                          # a file the chunk creates has no text to quote yet
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        created = {str(n).strip() for n in (c.get("new") or []) if isinstance(n, str)}
        syms = set()
        for s_ in (c.get("symbols") or []):
            name = str(s_).strip().split(" ")[0].split("(")[0].split("@")[0]
            if name:
                syms.add(name)
        for s_ in (c.get("symbols") or []):
            raw = str(s_).strip()
            if "(new)" in raw.lower():
                name = raw.split(" ")[0].split("(")[0].split("@")[0]
                if name:
                    created.add(name)
        for j, st in enumerate(steps, 1):

            if not isinstance(st, dict):
                continue
            where = str(st.get("where") or "").strip()
            m = _LINE_RANGE.match(where)
            if m:
                lo = int(m.group(1))
                hi = int(m.group(2) or lo)
                win = (max(1, lo - STEP_LINE_SLACK), hi + STEP_LINE_SLACK)
            else:
                if where not in syms:
                    out.append(("brief-steps", i, f, f"step {j}: where {where!r} is not on the chunk's "
                                                     f"SYMBOLS line {sorted(syms)} -- name a listed symbol "
                                                     f"or L<start>-L<end>"))
                    continue
                if where in created:
                    out.append(("brief-steps", i, f, f"step {j}: where {where!r} is a symbol this chunk "
                                                     f"creates, so the file has no text there yet -- anchor "
                                                     f"the step at the existing neighbour it goes beside"))
                    continue
                try:
                    spans = auto_merge.symbol_spans(text, {where}) if f.endswith(".py") else []
                except SyntaxError:
                    spans = []
                if not spans:
                    out.append(("brief-steps", i, f, f"step {j}: where {where!r} is not a symbol defined in "
                                                     f"{f} -- for a non-Python file use L<start>-L<end>"))
                    continue
                lo, hi = spans[0]
                win = (lo, hi)
            now = _norm_lines(st.get("now") or "")
            if not now:
                out.append(("brief-steps", i, f, f"step {j}: now is empty -- copy the lines at {where} "
                                                 f"from the file as it is today"))
            elif not _holds(_norm_lines("\n".join(lines[win[0] - 1:win[1]])), now):
                out.append(("brief-steps", i, f, f"step {j}: its now is not the text of {f} at {where} "
                                                 f"(lines {lo}-{hi}) -- copy the lines from the file as it "
                                                 f"is today (read_file), never from memory"))
    return out


def lint(text, repo=REPO, execute=True, require_steps=False):
    """(chunks, findings). A block bridge refuses is one chunks-invalid finding per error."""
    import bridge
    chunks, errors = bridge.parse(text)
    if not errors:
        errors = bridge.validate(chunks, repo)
    if errors:
        return chunks, [("chunks-invalid", -1, "", e) for e in errors]
    findings = static(chunks, repo)
    findings += unanchored(chunks, repo)
    findings += steps_findings(chunks, repo, require=require_steps)
    if execute:
        findings += run_oracles(chunks, repo)
    return chunks, findings


CHECK_NAMES = ("chunks-invalid", "oracle-gate", "oracle-no-runner", "oracle-green", "oracle-crash",
               "oracle-red-reason", "oracle-dirty", "brief-anchor", "brief-unanchored", "brief-window", "brief-unresolved",
               "brief-overlap", "brief-steps", "oracle-weak", "brief-shared-target", "brief-skill-evidence", "oracle-live-state")


def record(task, answer, findings, executed=True):
    """One scope_quality event per check: passed, or failed with its class and evidence."""
    import scope_quality
    scope = scope_quality.scope_of(task, answer=answer)
    failed = {cls for cls, *_ in findings}
    for cls, i, f, ev in findings:
        scope_quality.record("lint", "failed", task=task, scope=scope, defect_class=cls,
                             evidence=f"chunk {i} {f}: {ev}", source="scope_lint")
    ran = CHECK_NAMES if executed else tuple(c for c in CHECK_NAMES if c not in (
        "oracle-green", "oracle-crash", "oracle-red-reason", "oracle-dirty"))
    for cls in ran:
        if cls not in failed:
            scope_quality.record("lint", "passed", task=task, scope=scope, check=cls, source="scope_lint")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("answer")
    ap.add_argument("--task", required=True)
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--no-run", action="store_true")
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--require-steps", action="store_true",
                    help="a chunk with no steps is a finding (scope_gate passes this to live scopers)")
    a = ap.parse_args(argv)
    text = Path(a.answer).read_text(encoding="utf-8", errors="replace")
    if "```chunks" not in text:
        print(f"{a.task}: no chunks block -- nothing to lint")
        return 1
    _chunks, findings = lint(text, Path(a.repo), execute=not a.no_run,
                          require_steps=a.require_steps)
    for cls, i, f, ev in findings:
        print(f"{a.task}\t{cls}\tchunk {i}\t{f}\t{ev}")
    if a.record:
        record(a.task, a.answer, findings, executed=not a.no_run)
    if not findings:
        print(f"{a.task}: clean")
    return 2 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
