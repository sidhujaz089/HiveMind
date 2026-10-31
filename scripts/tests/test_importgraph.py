#!/usr/bin/env python3
"""The contract for "what does this script reach, by import, inside this repository?"

WHY IT IS ITS OWN MODULE. `t235` needs the harness inventory to see scripts that touch a model
THROUGH another one -- `scope_queue.py` calls `deliberate.deliberate()` and so contains no
`ollama`, no `11434`, no `/api/chat`, and is invisible to a signature sweep.

That was first dispatched as one brief asking for the whole change inside
`harness_inventory.py`. It burned 58 minutes and failed twice at exactly 16000 tokens --
`coder.py` cannot tell a truncated answer from a bad one and called it "unusable" (`t238`).
Every small Python brief today finished in 4-9 minutes. So the work is split: this module
answers the graph question and nothing else, and the inventory consumes it.

THE ORACLE IS WRITTEN HERE AND THE IMPLEMENTATION IS DELEGATED. coder.py is given
scripts/importgraph.py and never this file.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


try:
    import importgraph
except Exception as e:                                       # noqa: BLE001
    print(f"  [BAD]  scripts/importgraph.py does not import: {type(e).__name__}: {e}")
    print("         Required:")
    print("           imports_of(path) -> set of repo-relative .py paths that file imports")
    print("           reaches(path) -> set of repo-relative .py paths reachable transitively")
    print("         Both resolve names only to files inside this repository. reaches() must")
    print("         terminate on an import cycle and must not include the file itself.")
    print("\n0 passed, 1 failed\n")
    sys.exit(1)

imports_of = getattr(importgraph, "imports_of", None)
reaches = getattr(importgraph, "reaches", None)
check("importgraph.imports_of exists", callable(imports_of), True)
check("importgraph.reaches exists", callable(reaches), True)
if not (callable(imports_of) and callable(reaches)):
    print(f"\n{PASSED} passed, {FAILED + 1} failed\n")
    sys.exit(1)

SQ = "scripts/scope_queue.py"

print("\nDirect imports are found, and only the ones inside this repository:")
direct = imports_of(SQ)
check("scope_queue imports jsonx", "scripts/jsonx.py" in direct, True)
check("scope_queue imports runledger", "scripts/runledger.py" in direct, True)
check("scope_queue imports modellease", "scripts/modellease.py" in direct, True)
check("scope_queue imports deliberate, which lives in another project folder",
      "projects/local-inference/deliberate.py" in direct, True)
check("stdlib is not a repo file", any("json" == Path(x).stem for x in direct), False)
check("a file does not import itself", SQ in direct, False)

print("\nreaches() is transitive -- one hop is not enough for an iceberg:")
r = reaches(SQ)
check("it reaches deliberate directly", "projects/local-inference/deliberate.py" in r, True)
check("it is a superset of the direct imports", direct.issubset(r), True)
check("and still does not include itself", SQ in r, False)

print("\nA leaf reaches nothing, and that is not an error:")
leaf = reaches("scripts/jsonx.py")
check("jsonx imports nothing from this repo", leaf, set())

print("\nAn import CYCLE terminates rather than recursing forever:")
# Not hypothetical: settings/models/console in projects/local-inference cross-import. If this
# hangs or blows the stack, the inventory built on it can never run in a check.
try:
    got = reaches("projects/local-inference/settings.py")
    ok = isinstance(got, set)
except RecursionError:
    ok = "RecursionError"
check("reaches() returns a set rather than exploding", ok, True)

print("\nA path that does not exist is a quiet empty answer, not a crash:")
check("a missing file reaches nothing", reaches("scripts/does_not_exist.py"), set())

print("\nThe answer is stable -- asking twice gives the same set:")
check("reaches() is deterministic", reaches(SQ), r)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
