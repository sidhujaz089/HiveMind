#!/usr/bin/env python3
"""The board reads the manager's declared step, and no step is not a scope (Jas, 2026-09-29:
"I see 43 scoped"; 3 of them had declared none and 1 had "(a): ALREADY DONE")."""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "scripts")]
import task_board as tb  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


report("(c) SCOPE", tb._step("# t1 -- step (c) SCOPE\n"), "c")
report("**Step taken: (c) SCOPE.**", tb._step("**Step taken: (c) SCOPE.**"), "c")
report("colon after the letter: (a): ALREADY DONE", tb._step("# t235 -- step (a): ALREADY DONE in code"), "a")
report("dash after the letter: (b) — NEEDS JAS", tb._step("(b) — NEEDS JAS"), "b")
report("no step declared -> None, not scoped", tb._step("I've exhausted the tool budget. Here is the triage"), None)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
