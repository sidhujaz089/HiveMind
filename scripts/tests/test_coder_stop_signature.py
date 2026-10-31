"""t1109: coder.py must not sign two TESTS-FAILED attempts as identical when the
suites that broke are different.

VERIFIED on main by running the real function (2026-10-13):

    _fail_sig({"outcome": "TESTS-FAILED", "broke": [TARGET], "output": "assert 1"})
      -> ('TESTS-FAILED', '')
    _fail_sig({"outcome": "TESTS-FAILED", "broke": [UNRELATED], "output": "assert 2"})
      -> ('TESTS-FAILED', '')      # EQUAL -- and that is the defect

The signature is (outcome, digit-normalised detail), and the TESTS-FAILED record built
in the attempt loop has NO "detail" key -- it carries "broke", "ops" and "output". So
every test-failing attempt signs ('TESTS-FAILED', '') and the loop's stop condition ends
the run after ANY two test-failing attempts, whatever they broke.

The dispatch that filed this, p445-t1046-session-review, 2026-10-13T16:24:34Z:

  attempt 1 ... tests fail (1: test_finding_discharge_evidence.py, 73s)  <- the target suite
  attempt 4 ... tests fail (1: test_slot_concurrency.py, 182s)           <- an UNRELATED suite
  stopping: two identical TESTS-FAILED failures

Attempt 4's list does not name the target suite: it had moved from red to green. The
harness called the two attempts one failure, stopped, and discarded a patch that had
solved the task.

Run: python3 scripts/tests/test_coder_stop_signature.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "projects" / "local-inference" / "coder.py"


def _coder():
    spec = importlib.util.spec_from_file_location("coder_under_test", SRC)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {SRC} -- the path is wrong")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["coder_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


# The two suite paths from the dispatch that filed t1109, used here as DISTINCT data
# values. They are never imported: what is under test is that the signature tells them
# apart, not that these files exist. Both do exist on disk under scripts/tests/.
TARGET = "scripts/tests/test_finding_discharge_evidence.py"
UNRELATED = "scripts/tests/test_slot_concurrency.py"


def _failed(n, broke, output):
    """A TESTS-FAILED record in the shape coder.py builds in its attempt loop: 'broke'
    is the list of newly-failing suite paths; there is no 'detail' key in it."""
    return {"n": n, "tokens": 1000, "seconds": 73.0, "outcome": "TESTS-FAILED",
            "broke": list(broke), "ops": [{"path": "scripts/x.py", "op": "replace"}],
            "output": output}


def test_breaking_the_target_is_not_breaking_an_unrelated_suite():
    """RED ON MAIN: the defect this task is about."""
    c = _coder()
    a = c._fail_sig(_failed(1, [TARGET], "assert 1 == 2"))
    b = c._fail_sig(_failed(4, [UNRELATED], "assert 3 == 4"))
    assert a != b, (
        "t1109: two TESTS-FAILED attempts that broke DIFFERENT suites share one "
        "signature " + repr((a, b)) + ". The attempt loop stops on 'two identical "
        "TESTS-FAILED failures' and discards a patch whose target suite turned green. "
        "The suites named in the record's broke field must be part of the signature.")


def test_breaking_the_target_plus_a_second_suite_is_not_the_target_alone():
    """RED ON MAIN: the same defect, the superset case the trace did not show."""
    c = _coder()
    a = c._fail_sig(_failed(1, [TARGET], "assert 1 == 2"))
    b = c._fail_sig(_failed(2, [TARGET, UNRELATED], "assert 1 == 2\nassert 3 == 4"))
    assert a != b, (
        "t1109: breaking the target suite alone, and breaking it plus " + UNRELATED
        + ", are different facts -- a run that stops on that pair stops on a failure "
        "that has GROWN. Both signed " + repr((a, b)) + ".")


def test_breaking_the_same_suite_is_still_identical_despite_different_output():
    """THE OVER-CORRECTION GUARD. The stop condition is real: two attempts that break
    the SAME suite are a model looping on one breakage, and the run must end. A build
    that keys on the per-attempt output text -- which differs on every attempt, as the
    two strings below do -- fails here and silently deletes the stop condition."""
    c = _coder()
    a = c._fail_sig(_failed(2, [UNRELATED], "FAIL test_slot_concurrency.py line 41: 7 != 9"))
    b = c._fail_sig(_failed(3, [UNRELATED], "FAIL test_slot_concurrency.py line 52: 12 != 15"))
    assert a == b, (
        "t1109 over-correction: two attempts that broke the SAME suite no longer "
        "compare identical " + repr((a, b)) + ". The 'two identical failures' stop "
        "condition can then never fire on a looping model, and it burns every attempt. "
        "Only WHICH suites broke may enter the signature -- not the per-attempt output "
        "text, which differs every time.")


def test_the_same_suites_reported_in_a_different_order_are_identical():
    """'broke' is built by iterating suites and the target is forced to the front of
    it, so its order is not stable. Two attempts that broke the same suites are one
    failure even when reported in the opposite order."""
    c = _coder()
    a = c._fail_sig(_failed(2, [TARGET, UNRELATED], "x"))
    b = c._fail_sig(_failed(3, [UNRELATED, TARGET], "x"))
    assert a == b, (
        "t1109 over-correction: the same set of broken suites, reported in a different "
        "order, signed differently " + repr((a, b)) + " -- the stop condition would "
        "miss a genuine repeat. Treat 'broke' as a set.")


def test_a_record_with_no_broke_key_keeps_todays_signature():
    """OVER-CORRECTION GUARD for every other outcome. A WONT-COMPILE record carries
    'detail', not 'broke', and its stop condition is the digit-normalised detail -- two
    compile failures on different lines are the same failure, which is why the digits
    are folded. A build that changes signatures for records without 'broke' fails here."""
    c = _coder()
    a = c._fail_sig({"outcome": "WONT-COMPILE", "detail": "e: line 41: 7 expected"})
    b = c._fail_sig({"outcome": "WONT-COMPILE", "detail": "e: line 92: 12 expected"})
    assert a == b, (
        "t1109 over-correction: a record with no 'broke' key no longer signs as it does "
        "on main " + repr((a, b)) + ". The digit normalisation of 'detail' is the stop "
        "condition for compile failures; leave it alone.")


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
