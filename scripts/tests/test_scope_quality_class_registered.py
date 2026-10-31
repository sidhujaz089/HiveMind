"""t752 -- a new scope-lint defect class must be registered as a SCOPE FAULT.

scope_quality.py records scope_fault as `defect_class in SCOPE_CLASSES` and report() prints
`  (not the scope)` next to any class that is not in it. A check whose class is unregistered fires,
writes rows, and moves no score: the defect is detected and unblamed, which is the failure this
repository has already documented as the expensive one.

RED ON MAIN for one reason: SCOPE_CLASSES has no "oracle-computed-expectation" entry, so the set
intersection below is empty and the first test fails.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import scope_quality

NEW = "oracle-computed-expectation"


def test_the_class_is_a_scope_fault(tmp):
    assert scope_quality.SCOPE_CLASSES & {NEW} == {NEW}, sorted(scope_quality.SCOPE_CLASSES)


def test_the_class_is_not_an_unblamed_class(tmp):
    # A class on both sides would be attributed to the harness, not the scope: the score would
    # count the defect as nothing. It belongs in exactly one set.
    assert NEW not in scope_quality.OTHER_CLASSES, sorted(scope_quality.OTHER_CLASSES)


def main():
    tests = [f for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for fn in tests:
        try:
            with tempfile.TemporaryDirectory(prefix="t752-") as d:
                fn(Path(d))
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
        else:
            print("ok %s" % fn.__name__)
    print("%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
