#!/usr/bin/env python3
"""t1122 -- flag-correction ledger + 14-day back-fill (FROZEN ORACLE: do not edit).

The metric Q16's appendix item 8 demands -- "in the last 14 days, how many times did you
act on a flag you later found was wrong?" -- has no collector. This suite pins one:
scripts/flag_corrections.py. The row shape is the RCA's, verbatim and in order
(workspace/rca/c200334a8e14.md:65); the back-fill is the join named at :66 (flag key +
action verb -> later correction signal, inside a date window).

Red on main: scripts/flag_corrections.py does not exist.
Run with plain python3 (pytest is not installed). Writes only inside temp dirs.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "flag_corrections.py"

# The RCA's row shape, verbatim and in order: workspace/rca/c200334a8e14.md:65
ROW_FIELDS = ("timestamp", "session", "flag_id", "action_taken",
              "later_found_wrong", "correction_action", "corrected_by")

FLAG = "t1041"
OTHER = "t1122"
START, END = "2026-09-25", "2026-10-08"


def load():
    if not MODULE.exists():
        raise AssertionError(
            "scripts/flag_corrections.py does not exist -- the module this task builds")
    sys.path.insert(0, str(ROOT / "scripts"))
    import flag_corrections
    return flag_corrections


def _rec(ts, sid, typ, text):
    return {"type": typ, "sessionId": sid, "timestamp": ts,
            "message": {"role": typ, "content": [{"type": "text", "text": text}]}}


def _write_transcript(directory, records):
    path = Path(directory) / "session.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")
    return path


def test_exports_the_contract():
    fc = load()
    for name in ("record_flag_action", "mark_corrected", "read_ledger",
                 "transcript_text", "back_fill"):
        assert callable(getattr(fc, name, None)), "flag_corrections has no callable " + name
    assert tuple(fc.ROW_FIELDS) == ROW_FIELDS, (
        "ROW_FIELDS must be the row shape named in workspace/rca/c200334a8e14.md:65, "
        "got %r" % (tuple(fc.ROW_FIELDS),))
    assert isinstance(fc.SIGNALS, tuple) and len(fc.SIGNALS) == 5, (
        "SIGNALS must be the five words named in the RCA, got %r" % (fc.SIGNALS,))
    for word in ("correct", "wrong", "revert", "undo", "wasn't"):
        assert word in fc.SIGNALS, "SIGNALS is missing the RCA's word %r" % word
    assert fc.FLAG_KEY.search(FLAG) and fc.FLAG_KEY.search(OTHER), (
        "FLAG_KEY must match task-shaped flag ids such as %s" % FLAG)


def test_record_writes_one_row_with_the_seven_named_fields():
    fc = load()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "flag-corrections.jsonl"
        row = fc.record_flag_action(FLAG, "filed the follow-up for the ledger flag",
                                    session="721de5a0", timestamp="2026-10-05T14:03:00Z",
                                    ledger=ledger)
        assert tuple(row) == ROW_FIELDS, (
            "row keys %r are not the contract order %r" % (tuple(row), ROW_FIELDS))
        assert row["flag_id"] == FLAG
        assert row["action_taken"] == "filed the follow-up for the ledger flag"
        assert row["session"] == "721de5a0"
        assert row["timestamp"] == "2026-10-05T14:03:00Z"
        assert row["later_found_wrong"] is False, (
            "a fresh record has not been corrected: later_found_wrong must be False, got %r"
            % (row["later_found_wrong"],))
        assert not row["correction_action"], "a fresh record has no correction_action"
        assert not row["corrected_by"], "a fresh record has no corrected_by"
        lines = ledger.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1, "one record must write one ledger line, got %d" % len(lines)
        assert json.loads(lines[0])["flag_id"] == FLAG, "the row must be one JSON object per line"


def test_record_appends_and_keeps_earlier_rows():
    fc = load()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "flag-corrections.jsonl"
        fc.record_flag_action(FLAG, "filed t1094 to close the debt",
                              session="s1", timestamp="2026-10-05T14:03:00Z", ledger=ledger)
        fc.record_flag_action(OTHER, "merged the flag's gate script",
                              session="s2", timestamp="2026-10-06T08:00:00Z", ledger=ledger)
        rows = fc.read_ledger(ledger)
        assert len(rows) == 2, "the writer appends: expected 2 rows, got %d" % len(rows)
        assert [r["flag_id"] for r in rows] == [FLAG, OTHER], (
            "rows must keep their distinct flag ids in write order, got %r"
            % [r["flag_id"] for r in rows])
        assert rows[0]["action_taken"] == "filed t1094 to close the debt", (
            "the first row's action text was rewritten by a later write")
        assert rows[1]["session"] == "s2", "each row keeps its own session"


def test_mark_corrected_fills_the_row_it_names():
    fc = load()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "flag-corrections.jsonl"
        fc.record_flag_action(FLAG, "merged branch for the ledger flag",
                              session="s1", timestamp="2026-10-05T14:03:00Z", ledger=ledger)
        fc.mark_corrected(FLAG, "the flag was wrong; the merge was reverted",
                          corrected_by="user", timestamp="2026-10-06T09:00:00Z",
                          ledger=ledger)
        rows = fc.read_ledger(ledger)
        assert len(rows) == 1, (
            "a correction fills the existing row, it does not append a second one: %d rows"
            % len(rows))
        row = rows[0]
        assert row["later_found_wrong"] is True, (
            "mark_corrected must set later_found_wrong True, got %r" % (row["later_found_wrong"],))
        assert row["correction_action"] == "the flag was wrong; the merge was reverted"
        assert row["corrected_by"] == "user"
        assert row["flag_id"] == FLAG, "the correction must not move the row to another flag"
        assert row["action_taken"] == "merged branch for the ledger flag", (
            "the correction must keep the original action text")
        fc.mark_corrected("t9999", "wasn't right at all", corrected_by="user", ledger=ledger)
        assert len(fc.read_ledger(ledger)) == 1, (
            "a correction naming a flag that was never acted on must create no row")


def test_back_fill_joins_action_to_a_later_correction():
    fc = load()
    verb = fc.ACTION_VERBS[0]
    signal = fc.SIGNALS[1] if len(fc.SIGNALS) > 1 else fc.SIGNALS[0]
    action_a = "We %s %s after the review closed, which shipped the gate." % (verb, FLAG)
    fix_a = "%s was %s: the merge was undone and the gate re-opened." % (FLAG, signal)
    action_b = "We closed %s on the strength of the panel note." % OTHER
    early = "We added %s back in August, before the window." % FLAG
    no_key = "that was the %s approach on the panel appendix." % signal
    with tempfile.TemporaryDirectory() as td:
        transcript = _write_transcript(td, [
            _rec("2026-10-05T14:03:00Z", "721de5a0", "assistant", action_a),
            _rec("2026-10-06T09:00:00Z", "721de5a0", "user", fix_a),
            _rec("2026-10-07T10:00:00Z", "721de5a0", "assistant", action_b),
            _rec("2026-08-01T10:00:00Z", "721de5a0", "assistant", early),
            _rec("2026-10-07T11:00:00Z", "721de5a0", "user", no_key),
        ])
        ledger = Path(td) / "flag-corrections.jsonl"
        rows = fc.back_fill([transcript], START, END, ledger=ledger)
        by_flag = {r["flag_id"]: r for r in rows}
        assert set(by_flag) == {FLAG, OTHER}, (
            "the back-fill must produce one row per flag acted on inside the window "
            "and none for the out-of-window line or the signal with no flag key; got %r"
            % sorted(by_flag))
        assert by_flag[FLAG]["later_found_wrong"] is True, (
            "flag %s was corrected inside the window: later_found_wrong must be True, got %r"
            % (FLAG, by_flag[FLAG]["later_found_wrong"]))
        assert by_flag[FLAG]["correction_action"] == fix_a
        assert by_flag[FLAG]["corrected_by"] == "user"
        assert by_flag[FLAG]["action_taken"] == action_a
        assert by_flag[FLAG]["session"] == "721de5a0"
        assert by_flag[FLAG]["timestamp"] == "2026-10-05T14:03:00Z", (
            "the row is stamped at the action, not at the correction")
        assert by_flag[OTHER]["later_found_wrong"] is False, (
            "flag %s has no later correction: it must stay False" % OTHER)
        assert by_flag[OTHER]["correction_action"] == "" and by_flag[OTHER]["corrected_by"] == ""
        assert len(rows) == 2, "expected exactly 2 rows, got %d" % len(rows)
        written = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        assert len(written) == 2, (
            "back_fill must persist the rows it finds, not only return them: %d lines" % len(written))
        assert {r["flag_id"] for r in written} == {FLAG, OTHER}, (
            "the ledger must carry the same events the scan found")
        assert len(fc.back_fill([transcript], START, END, ledger=ledger)) == 0, (
            "a second pass over the same window must not re-append rows already in the ledger")


def test_recorded_values_are_never_truncated():
    fc = load()
    verb = fc.ACTION_VERBS[0]
    long_sentence = ("We %s %s to close the debt: " % (verb, FLAG)
                     + "gate-6 audit evidence for the panel " * 12
                     + "and the panel can now check it by hand.")
    assert len(long_sentence) > 200, "the fixture sentence must be long enough to truncate"
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "flag-corrections.jsonl"
        transcript = _write_transcript(td, [
            _rec("2026-10-05T14:03:00Z", "721de5a0", "assistant", long_sentence)])
        rows = fc.back_fill([transcript], START, END, ledger=ledger)
        assert len(rows) == 1, "expected 1 row, got %d" % len(rows)
        assert rows[0]["action_taken"] == long_sentence, (
            "action_taken is a recorded value and must be the whole sentence, not a slice")


def test_transcript_text_handles_the_real_shapes():
    fc = load()
    assert "hello" in fc.transcript_text(
        _rec("2026-10-05T00:00:00Z", "s", "assistant", "hello"))
    plain = {"type": "user", "timestamp": "2026-10-05T00:00:00Z",
             "message": {"role": "user", "content": "plain string content"}}
    assert "plain string content" in fc.transcript_text(plain), (
        "content may be a plain string, as it is for user records")
    assert fc.transcript_text({}) == "", "a record with no message must yield empty text"
    assert fc.transcript_text("not a dict") == "", "a non-dict record must yield empty text"
    assert fc.transcript_text({"message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {}}]}}) == "", (
        "only text blocks are transcript text; a tool call is not")


def main():
    tests = [(name, fn) for name, fn in sorted(list(globals().items()))
             if name.startswith("test_") and callable(fn)]
    assert tests, "the oracle defines no tests"
    failed = []
    for name, fn in tests:
        try:
            fn()
            print("PASS", name)
        except Exception as exc:
            failed.append(name)
            print("FAIL %s -> %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d passed" % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
