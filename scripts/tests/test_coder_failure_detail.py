"""t1323: a TESTS-FAILED dispatch record must carry the reason the tests failed.

Filed from p563-t800-flow-coverage on swift, 2026-10-16T21:33:41Z: the final status line read
`last: TESTS-FAILED --` with nothing after the dash. Read on 2026-10-17 (numbered reads,
projects/local-inference/coder.py, 3148 lines):

  3082  the TESTS-FAILED attempt row is appended with n, tokens, seconds, outcome, broke,
        ops, output -- and NO "detail" key. Every other outcome in the same loop builds one.
  2362  record()'s per-attempt note line appends the detail sliced to 180 characters, and for
        a row with no detail it appends nothing at all.
  1163  _fail_sig signs a failed attempt from str(row.get("detail", "")) -- so an empty detail
        signs every test-failing attempt identically (t1109).
  end of main(): the closing status line prints tries[-1].get('detail', '') -> "" for TESTS-FAILED.

WHAT THIS SUITE PINS -- the record a person reads, not a return shape: record()'s note on the
task, the trail row in agent-runs.jsonl, and (through the same key) the closing status line.
The detail must name the failing test, carry its assertion line, and be written whole: a pytest
report puts the assertion at the END, which is exactly the part a fixed-length slice discards.

Run: python3 scripts/tests/test_coder_failure_detail.py
"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "projects" / "local-inference" / "coder.py"


def _load():
    spec = importlib.util.spec_from_file_location("coder_under_t1323", SRC)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load %s -- the path is wrong" % SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


coder = _load()

# Real, distinct values, not one repeated placeholder: a suite, a test inside it, the
# assertion line a reader has to see to act, and a marker sitting at the very end of the
# report -- the line a 180-character slice throws away.
SUITE = "scripts/tests/test_flow_coverage.py"
OTHER = "scripts/tests/test_slot_concurrency.py"
FAIL_LINE = "FAILED scripts/tests/test_flow_coverage.py::test_row_recorded_on_entry"
ASSERT_LINE = ("E   AssertionError: assert 'flow-coverage' in 'scope_queue' "
               "-- producer resolves to a script evidence.status() cannot map")
TAIL = "E   assert 'flow-coverage' in 'scope_queue' <<T1323-LAST-LINE-OF-FAILURE-OUTPUT>>"


def _output():
    """A pytest failure report longer than 180 characters, with the reason at the END."""
    filler = "\n".join("    captured stdout %d: context that is not the reason" % i
                       for i in range(9))
    return ("========================= FAILURES =========================\n"
            + FAIL_LINE + "\n" + filler + "\n" + ASSERT_LINE + "\n" + TAIL + "\n")


def _row(broke=None, output=None):
    """The row coder.py appends for a failing attempt, built the same way."""
    return {"n": 1, "tokens": 12000, "seconds": 73.0, "outcome": "TESTS-FAILED",
            "broke": [SUITE] if broke is None else list(broke),
            "ops": [{"path": SUITE, "old": "x", "new": "y"}],
            "output": _output() if output is None else output}


def _fn():
    fn = getattr(coder, "failure_detail", None)
    assert callable(fn), ("coder.py has no failure_detail(...) -- the TESTS-FAILED attempt "
                          "row carries no 'detail' key, so the closing status line, record()'s "
                          "note and _fail_sig all report nothing")
    return fn


def test_failure_detail_exists():
    _fn()


def test_detail_names_the_failing_test_and_its_assertion():
    detail = _fn()(_row())
    assert isinstance(detail, str) and detail.strip(), "detail must be a non-empty str"
    assert FAIL_LINE in detail, repr(detail)
    assert ASSERT_LINE in detail, repr(detail)
    assert "::test_row_recorded_on_entry" in detail, (
        "the detail names the file only -- the reader cannot tell WHICH test failed: "
        + repr(detail))
    # NEGATIVE: naming the outcome is not naming a reason.
    assert detail.strip() not in ("TESTS-FAILED", "-", "—"), repr(detail)


def test_detail_is_not_cut_before_the_reason():
    row = _row()
    out = row["output"]
    assert len(out) > 180, "the fixture is too short to expose a 180-character cut"
    assert out.index(TAIL) > 180, "the reason must sit past the 180-char mark in the fixture"
    detail = _fn()(row)
    assert TAIL in detail, (
        "the detail is cut before the end of the failure report -- the assertion line at the "
        "end is the part a fixed-length slice discards: " + repr(detail[-120:]))
    assert detail.index(TAIL) > 180, "the detail keeps the header and loses the reason: " + repr(detail)


def test_detail_is_stable_across_stdout_chatter_and_suite_order():
    fn = _fn()
    chatter = _output().replace("context that is not the reason", "context 7 in a later run")
    assert fn(_row()) == fn(_row(output=chatter)), (
        "the detail must be stable when only captured stdout differs -- _fail_sig signs "
        "attempts from the detail, and scripts/tests/test_coder_stop_signature.py pins today "
        "that two attempts breaking the SAME suite are ONE failure: "
        + repr(fn(_row(output=chatter))[:80]))
    assert fn(_row(broke=[SUITE, OTHER])) == fn(_row(broke=[OTHER, SUITE])), (
        "suite order is not a new failure -- sort the suite names")
    assert fn(_row(broke=[OTHER])) != fn(_row(broke=[SUITE])), (
        "breaking a different suite is a different fact and must not sign identically")


def test_an_outcome_that_already_has_a_reason_keeps_it_word_for_word():
    fn = _fn()
    for row in ({"outcome": "WONT-COMPILE", "detail": "compile: NameError: 'x' is not defined"},
                {"outcome": "REFUSED", "detail": "path refused: scripts/nope.py"},
                {"outcome": "NO-OP", "detail": "ops=[] -- nothing was changed"}):
        assert fn(row) == row["detail"], (
            "an outcome that already builds a detail must keep its exact wording: "
            + repr(fn(row)))
    fallback = fn({"outcome": "NO-OP"})
    assert fallback.strip() and "NO-OP" in fallback, (
        "a row with neither detail nor output must still say what happened: " + repr(fallback))


def test_record_carries_the_reason_the_reader_needs():
    """The behaviour a person checks by hand: the note on the task, and the jsonl trail."""
    calls = []
    saved = (coder.RUNS, coder.sh, coder.write_session, coder.resolve_model)
    tmp = Path(tempfile.mkdtemp(prefix="t1323-"))
    try:
        coder.RUNS = tmp / "agent-runs.jsonl"
        coder.write_session = lambda *a, **kw: None
        coder.resolve_model = lambda role: "swift-under-test"

        def fake_sh(*args, **kw):
            calls.append(list(args))
            return ""

        coder.sh = fake_sh
        coder.record("t1323", "swift-under-test", "TESTS-FAILED", [_row()],
                     None, None, target=SUITE)
    finally:
        coder.RUNS, coder.sh, coder.write_session, coder.resolve_model = saved

    assert calls, "record() made no taskstore call -- the note is what a person reads"
    argv = calls[-1]
    assert argv[2] == "note", argv[:4]
    assert argv[3] == "t1323", argv[:4]
    note = argv[4]
    assert "TESTS-FAILED" in note, note
    assert FAIL_LINE in note, "the note names no failing test: " + repr(note)
    assert ASSERT_LINE in note, "the note carries no assertion detail: " + repr(note)
    assert TAIL in note, "the note is cut before the reason: " + repr(note[-160:])
    lines = [ln for ln in note.splitlines() if "attempt 1:" in ln]
    assert lines, "no attempt line in the note: " + repr(note)
    assert lines[0].split("TESTS-FAILED", 1)[1].strip(" -\u2014") != "", (
        "the attempt line names the outcome and no reason: " + repr(lines[0]))

    rows = [json.loads(x) for x in (tmp / "agent-runs.jsonl").read_text().splitlines()]
    trail = rows[0]["trail"][0]
    assert trail["outcome"] == "TESTS-FAILED", trail
    assert FAIL_LINE in trail["detail"], repr(trail["detail"])[:300]
    assert TAIL in trail["detail"], (
        "the machine record's detail is cut: " + repr(trail["detail"])[-160:])
    assert trail["output"] == _row()["output"], (
        "the full failure output stays recorded whole in the trail's output key")


if __name__ == "__main__":
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    _failed = 0
    for _t in _tests:
        try:
            _t()
            print("ok   %s" % _t.__name__)
        except Exception as _e:  # noqa: BLE001
            _failed += 1
            print("FAIL %s: %s: %s" % (_t.__name__, type(_e).__name__, _e))
    print("%d/%d passed" % (len(_tests) - _failed, len(_tests)))
    sys.exit(1 if _failed else 0)
