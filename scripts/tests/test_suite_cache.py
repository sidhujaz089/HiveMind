#!/usr/bin/env python3
"""t461: a suite's cached pass is keyed on ITS OWN inputs -- the suite file, what it imports, and the
scripts it names. Change any of them and the key changes; change something unrelated and it does
not. Simulated in memory: no file on disk is modified."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import run_tests as rt  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


suite = rt.TESTS / "test_board_step.py"        # imports scripts/task_board.py
k1, why = rt.suite_key(suite)
report("a key is computed from the suite's inputs", bool(k1) and "input file" in why, True)
report("the same inputs give the same key", rt.suite_key(suite)[0], k1)
real = Path.read_bytes


def patched(self, target):
    return lambda p: (real(p) + b"# changed\n") if p.name == target else real(p)


Path.read_bytes = patched(None, "task_board.py")
k2 = rt.suite_key(suite)[0]
# AN UNRELATED SCRIPT IS COMPUTED, not named (2026-10-01): "notify.py" was unrelated until the
# board learned to read supervision, and then the test failed on a true key change.
import importgraph                                           # noqa: E402
_inputs = {Path(p).name for p in importgraph.reaches(str(suite.relative_to(rt.REPO)))}
_unrelated = next(p.name for p in sorted((rt.REPO / "scripts").glob("*.py")) if p.name not in _inputs)
Path.read_bytes = patched(None, _unrelated)                  # not an input of this suite
k3 = rt.suite_key(suite)[0]
Path.read_bytes = real
report("changing a module the suite imports changes the key", k2 != k1, True)
report("changing an unrelated script does not", k3, k1)
report("the sandbox (coder) suites never come from the cache",
       rt.cached_passes([rt.TESTS / "test_coder_guards.py"]), {})
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
