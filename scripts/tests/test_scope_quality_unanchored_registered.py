#!/usr/bin/env python3
"""t717 (2026-10-07): `brief-unanchored` is not registered in scope_quality.SCOPE_CLASSES, so
scope_quality.record writes every row for it with `scope_fault: False` (scope_quality.py:144:
`"class": defect_class, "scope_fault": defect_class in SCOPE_CLASSES,`) and
manager_prompt.scope_defect_classes, which filters on that field (manager_prompt.py:363:
`if r.get("scope_fault") and r.get("class"):`), never shows the class to the scoper.
28 refusals 2026-10-07..2026-10-11 (3/9/3/2/11 a day; swift 25, qwen3.8:27b-mtp-q4_K_M 3) were all
recorded and all invisible: the class can NEVER reach the scoper's prompt, at any count.
T1024's shape -- scripts/tests/test_t1024_scope_classes.py describes exactly this for a class
missing from SCOPE_CLASSES. Inverted per docs/skills/planning-for-sustainment: the fixture is not
`brief-unanchored is registered`, it is `every class scope_lint records is registered`, so the next
check added without registering is caught on the day it is added.

House style: report(name, got, want); run directly with python3 (pytest is not installed).
No subprocess, no model call, no network, no writes: imports and set arithmetic only.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import scope_quality as sq  # noqa: E402
import scope_lint as sl  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def test_brief_unanchored_is_a_scope_fault():
    """The class this task exists for. Unregistered, scope_quality.py:144 computes
    scope_fault=False for every row the lint emits for it."""
    report("brief-unanchored is in SCOPE_CLASSES", "brief-unanchored" in set(sq.SCOPE_CLASSES), True)


def test_a_class_the_lint_records_is_counted_as_a_scope_fault():
    """BEHAVIOUR, not membership syntax: a class is only visible to the scoper if scope_lint
    records it (scope_lint.CHECK_NAMES drives record() at scope_lint.py:493-510) AND SCOPE_CLASSES
    makes it a scope fault. Asserting the conjunction is what a person checks by hand: the row
    reaching the prompt needs both halves true."""
    report("scope_lint records it AND scope_quality counts it",
           ("brief-unanchored" in set(sl.CHECK_NAMES)) and ("brief-unanchored" in set(sq.SCOPE_CLASSES)),
           True)


def test_every_class_the_lint_records_is_registered():
    """THE DECAY TEST, and the reason to fix the set and not one string: scope_lint.record() writes
    one event per class in CHECK_NAMES (scope_lint.py:493-495, used at :504-510). Any class in that
    list that SCOPE_CLASSES omits is recorded scope_fault=False and is silently invisible to the
    scoper's prompt (manager_prompt.py:363) -- which is how 28 refusals piled up under a green lint."""
    missing = sorted(c for c in sl.CHECK_NAMES if c not in set(sq.SCOPE_CLASSES))
    report("every CHECK_NAMES class is in SCOPE_CLASSES", missing, [])


def test_registration_entries_are_clean_class_names():
    """scope_quality.py:144 tests membership by equality, so an entry carrying whitespace, a
    `@line` suffix or a non-string registers nothing -- the fix must be the bare class name."""
    bad = [c for c in sq.SCOPE_CLASSES if not isinstance(c, str) or not c.strip() or c != c.strip()]
    report("every SCOPE_CLASSES entry is a clean non-empty string", bad, [])


if __name__ == "__main__":
    test_brief_unanchored_is_a_scope_fault()
    test_a_class_the_lint_records_is_counted_as_a_scope_fault()
    test_every_class_the_lint_records_is_registered()
    test_registration_entries_are_clean_class_names()
    print(f"{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
