"""t1113: a suite the brief never named must not gate the verdict.

VERIFIED on main (2026-10-16, reading projects/local-inference/coder.py, 3148 lines):
the attempt loop builds `new_failures` from EVERY suite red after the patch
(coder.py:3070), forces the target in when it is red (coder.py:3071-3074), and accepts
only when that list is empty (coder.py:3075). A suite the brief never names -- not the
target, not named in the brief text -- therefore gates the run exactly as hard as the
target does. The dispatch that filed this task (p457-t1057-session-candidates,
2026-10-13T17:10:54Z) burned attempts 4 and 5 on scripts/tests/test_slot_concurrency.py,
a suite the model was never shown and cannot fix from inside a one-file brief, so the
loop could only regenerate the same patch.

The fix is a pure function `verdict(failed, named, target)` in coder.py that splits the
red suites into the ones the brief named (they gate) and the ones it did not (recorded,
reported, NOT a verdict). This suite pins that split.

The paths below are DATA, never imported: what is under test is which side of the line a
path falls on. Only coder.py itself is ever read from disk.

Run: python3 scripts/tests/test_coder_collateral_gate.py
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "projects" / "local-inference" / "coder.py"

TARGET = "scripts/tests/test_finding_7780bb2535a7_discharge.py"
NAMED = "scripts/tests/test_coder_stop_signature.py"
C1 = "scripts/tests/test_slot_concurrency.py"
C2 = "scripts/tests/test_coder_guards.py"
C3 = "scripts/tests/test_coder_scope_gate.py"

_MOD = None


def _coder():
    # Loaded ONCE. coder.py's module top level runs _resolve_gateway() (coder.py:144), a
    # localhost TCP probe with a 3s timeout; re-executing the module per test would pay it
    # nine times for no reason. Import has no side effects that matter here: no writes,
    # no subprocess, no model call; the CLI is behind the __main__ guard (3147-3148).
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("coder_under_test", SRC)
        if spec is None or spec.loader is None:
            raise AssertionError("cannot load %s -- the path is wrong" % SRC)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["coder_under_test"] = mod
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def _verdict():
    fn = getattr(_coder(), "verdict", None)
    if not callable(fn):
        raise AssertionError(
            "coder.py has no callable verdict(failed, named, target). t1113 needs it: the "
            "attempt loop must ask it which red suites the BRIEF named (those gate) and "
            "which are collateral (recorded, reported, not a verdict).")
    return fn


def test_a_collateral_only_failure_is_accepted():
    r = _verdict()(failed=[C1], named=[TARGET], target=TARGET)
    assert r["hard"] == [], "hard must be empty when the brief named nothing red: %r" % (r,)
    assert r["accepted"] is True, "target green + one undeclared suite red must ACCEPT: %r" % (r,)
    assert r["collateral"] == [C1], "the collateral suite must be recorded: %r" % (r,)
    assert C1 in r["note"] and C1.split("/")[-1] in r["note"], \
        "the note must name the collateral suite by path AND basename: %r" % (r,)
    assert "1" in r["note"], "the note carries the count of collateral suites: %r" % (r,)


def test_the_target_suite_is_never_collateral():
    r = _verdict()(failed=[TARGET, C1], named=[TARGET], target=TARGET)
    assert r["accepted"] is False, "a red target can never be accepted: %r" % (r,)
    assert r["hard"] == [TARGET], "the target must lead hard: %r" % (r,)
    assert r["collateral"] == [C1], \
        "collateral survives a red target -- it is the reviewer's evidence: %r" % (r,)


def test_the_target_is_hard_even_when_the_brief_never_names_it():
    r = _verdict()(failed=[TARGET], named=[], target=TARGET)
    assert r["accepted"] is False and r["hard"] == [TARGET], \
        "the target is never collateral, named or not: %r" % (r,)


def test_a_suite_the_brief_named_by_name_still_gates():
    r = _verdict()(failed=[NAMED, C1], named=[TARGET, NAMED], target=TARGET)
    assert r["accepted"] is False, "a suite the brief named keeps gating: %r" % (r,)
    assert r["hard"] == [NAMED], "only the named red suite is hard: %r" % (r,)
    assert r["collateral"] == [C1], "the unnamed one is collateral: %r" % (r,)


def test_every_collateral_suite_is_named_in_the_note():
    # never-truncate-a-record: the loop today shows only new_failures[:2].
    # A note that keeps that slice hides the third suite from the reviewer: it fails here.
    r = _verdict()(failed=[TARGET, C1, C2, C3], named=[TARGET], target=TARGET)
    assert r["accepted"] is False, "a red target gates regardless: %r" % (r,)
    assert r["collateral"] == sorted([C1, C2, C3]), \
        "every collateral suite is in the list, not the first two: %r" % (r,)
    for s in (C1, C2, C3):
        assert s in r["note"] and s.split("/")[-1] in r["note"], \
            "the note names EVERY collateral suite; a [:2] note hides %s: %r" % (s, r["note"])
    assert "3" in r["note"], "the note states the count, 3: %r" % (r,)


def test_a_clean_run_adds_no_noise():
    r = _verdict()(failed=[], named=[TARGET], target=TARGET)
    assert r["accepted"] is True and r["hard"] == [] and r["collateral"] == []
    assert r["note"] == "", "no collateral suites means an empty note: %r" % (r,)


def test_repeated_and_unsorted_inputs_agree():
    v = _verdict()
    a = v(failed=[C2, C1, C2], named={TARGET}, target=TARGET)
    b = v(failed=(C1, C2), named=[TARGET], target=TARGET)
    assert a["collateral"] == b["collateral"] == sorted([C1, C2]), (a["collateral"], b["collateral"])
    assert a["note"] == b["note"], "the note is order- and duplicate-independent: %r %r" % (a["note"], b["note"])
    assert a["accepted"] is True and b["accepted"] is True
    c = v(failed=[C1, NAMED, C2], named=[NAMED, TARGET], target=TARGET)
    d = v(failed=[C2, C1, NAMED], named=[TARGET, NAMED], target=TARGET)
    assert c["hard"] == d["hard"] == [NAMED], "hard is target-first, then sorted: %r %r" % (c["hard"], d["hard"])
    assert c["collateral"] == d["collateral"] == sorted([C1, C2])


def test_the_attempt_loop_actually_asks_it():
    # A function that exists and is never called is a decoration: the ACCEPTED branch is
    # the thing that must read the verdict, so the call site must exist in the file.
    src = SRC.read_text(encoding="utf-8", errors="replace")
    n = src.count("verdict(")
    assert n >= 2, ("verdict( appears %d time(s) in coder.py: the definition is present "
                    "but the attempt loop never calls it, so the gate is unchanged." % n)


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failed += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
        else:
            print("ok   %s" % name)
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
