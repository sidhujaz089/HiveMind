"""Oracle for t1038: a built deferral must carry the artefacts that satisfied it.

RCA workspace/rca/43f57082ab7e.md: the promise "I'll write the anomaly half as a second
oracle" (session 7a0e9f2d, turn 226 of 364) WAS kept -- scripts/triage.py carries this
session's two motivating incidents verbatim -- but no record joins that quote to the
artefact, so a kept promise reads exactly like an unkept one. The promise on disk is
workspace/session-reviews/findings/done/43f57082ab7e.json: kind "deferral", covered_by "",
where "assistant, turn 226 of 364". That is the shape this suite pins a provenance row for.

RED ON MAIN FOR ONE REASON: scripts/session_review.py has no DEFERRAL_LEDGER, no
deferral_resolution(), and its _keep() writes no deferral provenance row, so the first
attribute access below raises AttributeError. That is the change this brief makes.

No pytest (not installed), no model call, no subprocess, no network. NO LIVE STATE: every
module path this suite writes (FINDINGS, LEDGER, DEFERRAL_LEDGER) is redirected into a
temporary directory for the duration of the test, and _keep's task-note callback is passed
as a no-op lambda, so no workspace/task file is touched. Restored in finally.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import session_review as sr

# The real deferral, read from workspace/session-reviews/findings/done/43f57082ab7e.json.
KEY = "43f57082ab7e"
QUOTE = ("I'll write the anomaly half as a second oracle rather than changing the contract "
         "mid-dispatch \u2014 small briefs land in 4\u20139 minutes, big ones fail.")
WHERE = "assistant, turn 226 of 364"
SESSION = "7a0e9f2d-ee02-4c11-a758-ac9e171b4a6b"
TASK_ID = "t1038"

# Two DISTINCT artefact paths (the real one, and one the RCA says was never filed). The
# quote that names them uses the same path text so the existing FIX_FILE_RE can find it.
BUILT = "scripts/triage.py"
NOT_BUILT = "scripts/tests/test_triage.py"
BUILT_QUOTE = ("I'll write the anomaly half as a second oracle over scripts/triage.py so "
               "that small briefs land in 4-9 minutes and big ones do not fail.")

REQ = {"id": SESSION, "harness": "claude"}
REQ_FILE = Path("t1038.txt")


def _finding(quote=QUOTE, where=WHERE, covered_by="", kind="deferral"):
    """A finding dict shaped like a real row from workspace/session-reviews/findings/."""
    return {"kind": kind, "quote": quote, "where": where,
            "why_missed": "Write anomaly half as second oracle",
            "context": "Two more things worth filing from the last ten minutes:",
            "covered_by": covered_by}


def _entry(resolution_rows):
    assert len(resolution_rows) == 1, (
        "one ledger row on disk must produce exactly one report entry, got %r"
        % (resolution_rows,))
    return resolution_rows[0]


def test_deferral_ledger_sits_beside_the_finding_ledger():
    assert sr.DEFERRAL_LEDGER == sr.LEDGER.parent / "deferral-ledger.jsonl", (
        "the provenance rows belong in workspace/session-reviews/deferral-ledger.jsonl: "
        "%r" % (sr.DEFERRAL_LEDGER,))


def test_keeping_a_deferral_records_the_artefacts_that_satisfy_it():
    old = (sr.FINDINGS, sr.LEDGER, sr.DEFERRAL_LEDGER)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sr.FINDINGS = root / "findings"
        sr.LEDGER = root / "ledger.jsonl"
        sr.DEFERRAL_LEDGER = root / "deferral-ledger.jsonl"
        try:
            new, covered = sr._keep(
                REQ_FILE, REQ,
                [_finding(quote=BUILT_QUOTE), _finding(quote=QUOTE, kind="unresolved")],
                note_task=lambda *a: None)
        finally:
            sr.FINDINGS, sr.LEDGER, sr.DEFERRAL_LEDGER = old
        assert (new, covered) == (2, 0), (
            "_keep's return contract is unchanged by the added row: %r" % ((new, covered),))
        lines = [ln for ln in sr.DEFERRAL_LEDGER.read_text(encoding="utf-8").splitlines()
                 if ln.strip()]
        assert len(lines) == 1, (
            "a deferral writes one provenance row and a non-deferral writes none: %r"
            % (lines,))
        row = json.loads(lines[0])
        assert row["artefacts"] == [BUILT], (
            "the row must carry the artefact paths the finding text names, via FIX_FILE_RE: "
            "%r" % (row.get("artefacts"),))
        assert row["turn"] == 226, (
            "the turn that made the promise is part of the address, taken from 'where' "
            "(real value: %r): %r" % (WHERE, row.get("turn")))
        assert row["quote"] == BUILT_QUOTE, (
            "the quote is stored WHOLE -- no slice, no cap: %r" % (row.get("quote"),))
        assert row["session"] == SESSION, "the row points back at the session: %r" % (row,)
        assert row["key"] == sr._fkey(REQ_FILE.name, BUILT_QUOTE), (
            "the row is addressable by the same key as the finding file: %r" % (row.get("key"),))
        assert row["task_id"] == "", (
            "today this deferral has no tracker; recording it as unowned is the point: "
            "%r" % (row.get("task_id"),))
        json.dumps(row)


def test_a_deferral_whose_artefacts_all_exist_is_reported_delivered():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = root / "deferral-ledger.jsonl"
        for rel in (BUILT, NOT_BUILT):
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("built\n", encoding="utf-8")
        ledger.write_text(json.dumps({"key": KEY, "session": SESSION, "turn": 226,
                                      "quote": BUILT_QUOTE, "task_id": TASK_ID,
                                      "artefacts": [BUILT, NOT_BUILT]}) + "\n",
                          encoding="utf-8")
        entry = _entry(sr.deferral_resolution(ledger, root))
        assert entry["key"] == KEY, "the report names the finding: %r" % (entry,)
        assert entry["task_id"] == TASK_ID, (
            "the tracker is carried so a reader can close it: %r" % (entry,))
        assert entry["resolved"] is True, (
            "every recorded artefact exists under root, so this deferral is delivered: "
            "%r" % (entry,))
        assert entry["reason"] == "delivered", "exact reason string: %r" % (entry,)
        assert entry["missing"] == []
        assert entry["quote"] == BUILT_QUOTE, "the report quotes the promise whole: %r" % (entry,)


def test_a_deferral_with_a_missing_artefact_is_not_reported_delivered():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = root / "deferral-ledger.jsonl"
        built = root / BUILT
        built.parent.mkdir(parents=True, exist_ok=True)
        built.write_text("built\n", encoding="utf-8")
        ledger.write_text(json.dumps({"key": KEY, "session": SESSION, "turn": 226,
                                      "quote": BUILT_QUOTE, "task_id": TASK_ID,
                                      "artefacts": [BUILT, NOT_BUILT]}) + "\n",
                          encoding="utf-8")
        entry = _entry(sr.deferral_resolution(ledger, root))
        assert entry["resolved"] is False, (
            "one recorded artefact does not exist, so the promise is not closed: %r" % (entry,))
        assert entry["missing"] == [NOT_BUILT], (
            "the report must name the path that is absent, exactly as recorded: %r" % (entry,))
        assert entry["reason"] == "artefact missing", "exact reason string: %r" % (entry,)
        assert entry["task_id"] == TASK_ID, (
            "the open row keeps its tracker so the sweep can act on it: %r" % (entry,))


def test_a_deferral_with_no_artefact_recorded_is_not_reported_delivered():
    """THE NEGATIVE CASE THE RCA IS ABOUT: an empty artefacts list is not a delivery."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = root / "deferral-ledger.jsonl"
        ledger.write_text(json.dumps({"key": KEY, "session": SESSION, "turn": 226,
                                      "quote": QUOTE, "task_id": "", "artefacts": []}) + "\n",
                          encoding="utf-8")
        entry = _entry(sr.deferral_resolution(ledger, root))
        assert entry["resolved"] is False, (
            "a deferral with no artefact recorded must not read as kept: %r" % (entry,))
        assert entry["missing"] == [], "nothing was recorded, so nothing can be missing: %r" % (entry,)
        assert entry["reason"] == "no artefact recorded", "exact reason string: %r" % (entry,)


def test_the_report_names_a_torn_line_instead_of_skipping_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = root / "deferral-ledger.jsonl"
        ledger.write_text(
            json.dumps({"key": KEY, "session": SESSION, "turn": 226, "quote": QUOTE,
                        "task_id": TASK_ID, "artefacts": []}) + "\n"
            + '{"key": "43f57082ab7e", "artefacts": ["scripts/triage.py"\n',
            encoding="utf-8")
        try:
            sr.deferral_resolution(ledger, root)
        except ValueError as e:
            msg = str(e)
            assert "deferral-ledger.jsonl" in msg, (
                "the error must name the file whose row is torn: %r" % (msg,))
            assert "2" in msg, "the error must give the 1-based line number: %r" % (msg,)
        else:
            raise AssertionError("a torn ledger row must raise, not be skipped")


def test_a_missing_ledger_reports_empty_not_a_crash():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        assert sr.deferral_resolution(root / "deferral-ledger.jsonl", root) == [], (
            "no ledger today is a clean report, not an exception: %r"
            % (sr.deferral_resolution(root / "deferral-ledger.jsonl", root),))


def test_blank_lines_are_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = root / "deferral-ledger.jsonl"
        ledger.write_text(
            "\n" + json.dumps({"key": KEY, "session": SESSION, "turn": 226,
                               "quote": QUOTE, "task_id": "", "artefacts": []}) + "\n\n",
            encoding="utf-8")
        assert len(sr.deferral_resolution(ledger, root)) == 1, (
            "blank lines carry no row; a real one must still be reported")


if __name__ == "__main__":
    _tests = [v for k, v in sorted(globals().items())
              if k.startswith("test_") and callable(v)]
    _failed = 0
    for _t in _tests:
        try:
            _t()
            print("ok   %s" % _t.__name__)
        except Exception as _e:
            _failed += 1
            print("FAIL %s: %s: %s" % (_t.__name__, type(_e).__name__, _e))
    print("%d passed, %d failed" % (len(_tests) - _failed, _failed))
    sys.exit(1 if _failed else 0)
