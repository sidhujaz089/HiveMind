#!/usr/bin/env python3
"""Who actually calls each Phase 0 gate -- the evidence pack for Q16's audit.

    python3 scripts/gate_callers.py              the report
    python3 scripts/gate_callers.py --brief      the same, formatted to paste into a brief
    python3 scripts/gate_callers.py --self-test

WHY THIS EXISTS
---------------
Q16, writing its own panel report without sight of ours (`workspace/q16/
PLAN-COMMITTEE-from-q16-ANNOTATED.md`, section 1.1), set a standard stricter than the one we
had been applying to ourselves:

    a script referenced only by its own `__main__` block and by handover notes is
    BUILT, NOT CALLED, and must be treated as gate-not-closed until a live job
    exercises it.

It asked for, per gate, the single `grep -rn` proving the gate's script is invoked by a running
job rather than by a test. It could not run that grep -- it had no shell -- and said so rather
than inventing the answer. This script runs it.

WHAT IT DOES NOT DO
-------------------
It does not decide. It reports which entry points reach each gate and what kind of entry point
each one is; the ruling is Q16's. That split is deliberate and is the correction to the tenth
instance of the pattern in `PLAN-FOR-REVIEW-draft-five.md` Part 4: last time the brief told the
model to read files and run scripts it had no access to, so the only ways to comply were to
invent or to refuse. Here the model is asked to judge evidence that is already in front of it.

REACHABILITY IS BY IMPORT, PLUS A TEXT SCAN FOR THE SHELL
---------------------------------------------------------
`importgraph.reaches()` follows Python imports transitively, so it sees `console.py` ->
`modellease.py` even through an intermediate module. It CANNOT see `scripts/hooks/pre-commit`
calling `signature_gate.py`, because that is a shell script invoking python3 by path. Both are
real invocations, so both are counted, and the report says which kind each was -- a gate proven
only by a text match is weaker evidence than one proven by the import graph, and the reader is
entitled to know which they are looking at.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import importgraph                                            # noqa: E402

# The six gates as claimed closed in STATE.md, each named by the module whose invocation
# is what "closed" would mean. Where a gate claims two mechanisms, both are listed.
GATES = [
    ("1. Run accounting", ["scripts/runledger.py"]),
    ("2. Extractor migration", ["scripts/jsonx.py", "scripts/signature_gate.py"]),
    ("3. Quarantine enforcement", ["scripts/evidence.py"]),
    ("4. Old metrics marked", ["scripts/stamp_legacy.py"]),
    ("5. Interlock", ["scripts/modellease.py"]),
    ("6. Golden set", ["scripts/golden_score.py"]),
]

# Entry points, by how they actually start. The tiers are descriptive, not a verdict.
SERVICES = {
    "projects/local-inference/console.py": "agent-console.service",
    "projects/local-inference/chatui.py": "model-chat.service",
    "scripts/chat_agent.py": "chat-agent.service",
}
HOOKS = [
    "scripts/hooks/pre-commit",
    "scripts/hooks/commit-msg",
    "scripts/hooks/pre-push",
]
SESSION = [
    "scripts/session_start.py",
    "scripts/session_end.py",
]
# Jobs a person or the console starts by hand to do real work, as opposed to a test run.
DISPATCH = [
    "projects/local-inference/coder.py",
    "scripts/scope_queue.py",
    "projects/local-inference/deliberate.py",
]


def _unit_state(unit):
    """ActiveState of a user unit, or 'unknown' if systemctl cannot say."""
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show", unit, "--property=ActiveState", "--value"],
            capture_output=True, text=True, timeout=10)
        return (out.stdout or "").strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def entry_points():
    """Every entry point, as (path, kind, detail). Kind is what starts it."""
    out = []
    for path, unit in SERVICES.items():
        out.append((path, "service", f"{unit} [{_unit_state(unit)}]"))
    for path in HOOKS:
        out.append((path, "git-hook", "fires on every commit/push in this clone"))
    for path in SESSION:
        out.append((path, "session", "run by a person at the start/end of a session"))
    for path in DISPATCH:
        out.append((path, "dispatch", "started by hand or by the console to do real work"))
    return [(p, k, d) for p, k, d in out if (REPO / p).is_file()]


def _module_names(target):
    """The import spellings that would reach `target`, e.g. 'modellease'."""
    stem = Path(target).stem
    return [f"import {stem}", f"from {stem} import", f"{stem}.py"]


def text_callers(target):
    """Non-Python files that name the target -- the shell hooks importgraph cannot see."""
    found = []
    needle = Path(target).name
    for path in HOOKS:
        full = REPO / path
        if not full.is_file():
            continue
        for n, line in enumerate(full.read_text(errors="replace").splitlines(), 1):
            if needle in line and not line.lstrip().startswith("#"):
                found.append((path, n, line.strip()))
    return found


def direct_importers(target):
    """Every repo .py file that imports the target directly, entry point or not.

    Without this the report understates: a gate whose only consumer is another script
    reads as 'NOTHING' when the truth is a one-link chain, and the length of that chain
    is exactly what the audit is asking about. A consumer that is itself uncalled does
    not close a gate, but it is a different finding from no consumer at all.
    """
    out = []
    for path in sorted(REPO.glob("scripts/*.py")) + \
            sorted(REPO.glob("projects/local-inference/*.py")):
        rel = str(path.relative_to(REPO))
        if rel == target:
            continue
        if target in importgraph.imports_of(rel):
            out.append(rel)
    return out


def tests_naming(target):
    """Test suites that reference the target at all. Counted, never as proof."""
    stem = Path(target).stem
    hits = []
    tests = REPO / "scripts" / "tests"
    if not tests.is_dir():
        return hits
    pat = re.compile(rf"\b{re.escape(stem)}\b")
    for p in sorted(tests.glob("test_*.py")):
        if pat.search(p.read_text(errors="replace")):
            hits.append(str(p.relative_to(REPO)))
    return hits


def audit():
    """For each gate module: which entry points reach it, and how."""
    eps = entry_points()
    reach_cache = {p: importgraph.reaches(p) for p, _, _ in eps}
    report = []
    for gate, targets in GATES:
        for target in targets:
            exists = (REPO / target).is_file()
            importers = [(p, k, d) for p, k, d in eps
                         if target in reach_cache.get(p, set())]
            report.append({
                "gate": gate,
                "target": target,
                "exists": exists,
                "importers": importers,
                "text": text_callers(target),
                "direct": direct_importers(target),
                "tests": tests_naming(target),
            })
    return report


def _verdict_hint(row):
    """A one-word shape for the row. NOT a ruling -- Q16 makes those."""
    if not row["exists"]:
        return "MISSING"
    live = [k for _, k, _ in row["importers"]]
    if row["text"]:
        live.append("git-hook")
    if not live:
        return "NO-CALLER"
    if set(live) <= {"dispatch"}:
        return "DISPATCH-ONLY"
    return "REACHED"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brief", action="store_true",
                    help="format for pasting into a model brief")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    rows = audit()
    print("WHO CALLS EACH PHASE 0 GATE")
    print("=" * 78)
    print()
    print("Entry points considered (nothing else was treated as a caller):")
    for path, kind, detail in entry_points():
        print(f"  {kind:9} {path:45} {detail}")
    print()
    print("Reachability is transitive through Python imports (importgraph.reaches),")
    print("plus a literal text scan of the three git hooks, which are shell.")
    print()

    for row in rows:
        print("-" * 78)
        print(f"{row['gate']}   ->   {row['target']}   [{_verdict_hint(row)}]")
        if not row["exists"]:
            print("  FILE DOES NOT EXIST")
            continue
        if row["importers"]:
            print("  reached by import from:")
            for path, kind, detail in row["importers"]:
                print(f"    {kind:9} {path:45} {detail}")
        else:
            print("  reached by import from an entry point: NOTHING")
        others = [d for d in row["direct"]
                  if d not in [p for p, _, _ in row["importers"]]]
        if others:
            print("  imported directly by (not themselves entry points):")
            for d in others:
                print(f"    {d}")
        if row["text"]:
            print("  named in a git hook:")
            for path, n, line in row["text"]:
                print(f"    {path}:{n}: {line}")
        print(f"  test suites naming it: {len(row['tests'])}")
        for t in row["tests"]:
            print(f"    {t}")
    print("-" * 78)
    print()
    print("A test suite is listed for completeness and is NOT evidence a gate is called.")
    print("That distinction is the whole point of the audit.")
    print()
    print("WHAT 'REACHED' DOES NOT MEAN")
    print("-" * 78)
    print("REACHED proves the module is IMPORTED by a running job. It does not prove the")
    print("gate's enforcement logic is exercised on any relevant operation. A module can be")
    print("imported at the top of a live service and its check never called -- behind a flag,")
    print("inside a branch that is never taken, or in a try/except that swallows the refusal.")
    print("Import reachability is necessary and not sufficient.")
    print()
    print("Q16 named this as the place the NEXT dishonest instance will grow, in the audit")
    print("this script was written for: not a lie, but an omission that lets the reader's")
    print("inference do the work of a claim -- 'imported by two active services' read as")
    print("'enforced on every request'. It asked for this paragraph to be in the output, so")
    print("it is. To close the gap for a given gate, exercise the refusal against a live")
    print("holder and record what came back. That was done for gate 5 on 2026-09-09:")
    print("console.ask() returned done_reason='lease_held' while a real audit held the lease.")
    print()
    print(migration_evidence())
    return 0


def migration_evidence():
    """Gate 4 is a completed migration, not a live caller, so ask the right question.

    Q16's ruling: "The instrument is designed to answer the wrong question for this gate.
    It can tell me that no running job calls stamp_legacy.py (true, and expected for a
    completed migration), but it cannot tell me whether 70 rows were actually stamped or
    whether the content is byte-identical otherwise." It asked for the output of the
    migration, verified independently of the script that produced it. Git holds both
    sides, so this reads them rather than trusting either the script or STATE.md.
    """
    rel = "workspace/scoping-proposals.jsonl"
    out = subprocess.run(["git", "-C", str(REPO), "log", "--format=%H", "-1", "--", rel],
                         capture_output=True, text=True, timeout=60).stdout.strip()
    if not out:
        return f"GATE 4 MIGRATION: cannot check -- git knows no commit touching {rel}."

    def at(rev):
        r = subprocess.run(["git", "-C", str(REPO), "show", f"{rev}:{rel}"],
                           capture_output=True, text=True, timeout=60)
        return [json.loads(l) for l in r.stdout.splitlines() if l.strip()]

    try:
        before, after = at(f"{out}~1"), at(out)
    except (json.JSONDecodeError, ValueError) as e:
        return f"GATE 4 MIGRATION: cannot check -- {rel} does not parse either side: {e}"

    stamped = sum(1 for r in after if r.get("collector_version"))
    changed = sum(1 for x, y in zip(before, after)
                  if x != {k: v for k, v in y.items() if k != "collector_version"})
    lines = [
        "GATE 4, THE COMPLETED MIGRATION (the question the caller graph cannot ask)",
        "-" * 78,
        f"  commit          {out[:9]}",
        f"  rows before     {len(before)}",
        f"  rows after      {len(after)}",
        f"  rows stamped    {stamped}",
        f"  rows differing in any field OTHER than collector_version: {changed}",
    ]
    ok = len(before) == len(after) and changed == 0 and stamped == len(after)
    lines.append("  => content is byte-identical apart from the stamp" if ok
                 else "  => CONTENT CHANGED beyond the stamp. The migration did more than mark.")
    return "\n".join(lines)


def self_test():
    """Prove the report can say NO, not only yes.

    A checker that has only ever reported REACHED cannot be told apart from one that
    always reports REACHED (docs/CHECKS.md, rule 1).
    """
    ok = True

    print("The hint must distinguish a called gate from an uncalled one:")
    reached = _verdict_hint({"exists": True, "text": [],
                             "importers": [("x", "service", "d")], "tests": []})
    print(f"  [{'ok ' if reached == 'REACHED' else 'BAD'}]  a service importer reads REACHED: {reached}")
    ok &= reached == "REACHED"

    none = _verdict_hint({"exists": True, "text": [], "importers": [], "tests": ["t"]})
    print(f"  [{'ok ' if none == 'NO-CALLER' else 'BAD'}]  tests alone read NO-CALLER: {none}")
    ok &= none == "NO-CALLER"

    disp = _verdict_hint({"exists": True, "text": [],
                          "importers": [("x", "dispatch", "d")], "tests": []})
    print(f"  [{'ok ' if disp == 'DISPATCH-ONLY' else 'BAD'}]  a hand-started job is not a service: {disp}")
    ok &= disp == "DISPATCH-ONLY"

    missing = _verdict_hint({"exists": False, "text": [], "importers": [], "tests": []})
    print(f"  [{'ok ' if missing == 'MISSING' else 'BAD'}]  an absent file reads MISSING: {missing}")
    ok &= missing == "MISSING"

    print()
    print("The shell scan must find a hook calling a script by path:")
    hits = text_callers("scripts/signature_gate.py")
    print(f"  [{'ok ' if hits else 'BAD'}]  pre-commit calls signature_gate.py: {len(hits)} hit(s)")
    ok &= bool(hits)

    absent = text_callers("scripts/definitely_not_a_real_script.py")
    print(f"  [{'ok ' if not absent else 'BAD'}]  a script no hook names finds nothing: {len(absent)} hit(s)")
    ok &= not absent

    print()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
