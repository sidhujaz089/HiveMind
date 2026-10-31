#!/usr/bin/env python3
"""RED fixture for t809: session_end.py records file-retirement decisions at close.

The instance (workspace/rca/6bd275fb9ae7.md): near the end of session
6cc34e3b-0c16-4f3b-8f78-98893304830f on 2026-09-28 the user said "we decided to
delete those CSV files. they serve no purpose now." Nine days on, nothing on disk
records it -- workspace/retirements.jsonl does not exist -- so the files' live state
is unprovable. The close-out is the endpoint where a file's purpose ends.

Red on main for ONE reason: scripts/session_end.py defines no record_retirements().
`import session_end` SUCCEEDS on main (its module body is imports and constants only,
scripts/session_end.py:17-34); the attribute lookup fails. That AttributeError is the
whole red: the writer is the change. Nothing here touches live state -- every write
goes into a tempfile.TemporaryDirectory.

Run: python3 scripts/tests/test_session_end_writes_retirements.py
"""
import contextlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import session_end  # noqa: E402  the module exists; record_retirements does not

# Verbatim from the RCA, not invented phrasings: these are the sentences the writer
# exists to catch. A synthetic sentence would let an implementation match prose that
# has never occurred and miss this one.
CSV_SENTENCE = "we decided to delete those CSV files. they serve no purpose now."
MCP_SENTENCE = ("we aren't using projects in Chat now that it has a better memory "
                "with the MCP.")
# The row shape the RCA specifies: {ts, session, quote, actor, paths, reason, task,
# deleted:yes|no}. Pinned as a SET so a field silently dropped is a finding.
FIELDS = {"ts", "session", "quote", "actor", "paths", "reason", "task", "deleted"}
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _rows_of(p):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_writes_the_verbatim_retirement_row_to_the_path_it_is_given():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "retirements.jsonl"
        n = session_end.record_retirements([{"role": "user", "text": CSV_SENTENCE}],
                                           session="6cc34e3b", out_path=p)
        assert n == 1, f"expected 1 row, got {n!r}"
        rows = _rows_of(p)
        assert len(rows) == 1, f"1 row expected on disk, got {len(rows)}"
        row = rows[0]
        assert set(row) == FIELDS, f"row must carry exactly the RCA's fields: {sorted(row)!r}"
        assert row["quote"] == CSV_SENTENCE, f"quote must be the sentence whole: {row['quote']!r}"
        assert row["actor"] == "user", f"actor: {row['actor']!r}"
        assert row["session"] == "6cc34e3b", f"session: {row['session']!r}"
        assert row["deleted"] == "no", f"deleted: {row['deleted']!r}"
        assert row["task"] == "", f"task defaults to empty for the companion check to catch: {row['task']!r}"
        assert row["paths"] == [], f"this sentence names no file path: {row['paths']!r}"
        assert "serve no purpose" in str(row["reason"]).lower(), \
            f"reason must name the phrasing that matched: {row['reason']!r}"
        assert ISO.match(str(row["ts"])), f"ts is not an ISO timestamp: {row['ts']!r}"


def test_writes_no_file_and_returns_zero_when_nothing_retires():
    # NEGATIVE assertion: a close with no retirement language must leave no ledger. A
    # file written empty is the header-with-no-body record this repository has a skill
    # about -- it reads as coverage while nothing was captured.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "retirements.jsonl"
        n = session_end.record_retirements(
            [{"role": "user", "text": "the tests pass, I committed the change."}],
            session="s", out_path=p)
        assert n == 0, f"expected 0 rows, got {n!r}"
        assert not p.exists(), f"nothing retired must create no file: {p}"


def test_records_both_voices_under_their_own_actor():
    # The RCA's change (a) says USER and assistant. Two DISTINCT sentences, so an
    # implementation that hard-codes one actor, or drops the user voice, fails here.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "retirements.jsonl"
        n = session_end.record_retirements(
            [{"role": "user", "text": CSV_SENTENCE},
             {"role": "assistant", "text": MCP_SENTENCE}],
            session="s1", out_path=p)
        assert n == 2, f"expected 2 rows, got {n!r}"
        rows = _rows_of(p)
        assert [r["actor"] for r in rows] == ["user", "assistant"], \
            f"voice must be preserved per row: {[r['actor'] for r in rows]!r}"
        assert rows[0]["quote"] == CSV_SENTENCE, f"row 0 quote: {rows[0]['quote']!r}"
        assert rows[1]["quote"] == MCP_SENTENCE, f"row 1 quote: {rows[1]['quote']!r}"
        assert "aren't using" in rows[1]["reason"].lower(), \
            f"assistant-voice phrasing must match too: {rows[1]['reason']!r}"


def test_paths_named_in_the_sentence_are_recorded_verbatim():
    # 'those CSV files' is the referent this ledger exists to pin down (DE-1). Where the
    # sentence DOES name files, the names go in the row, in order, unedited.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "retirements.jsonl"
        text = ("we decided to delete chat/export/routing.csv and chat/export/lookup.csv. "
                "they serve no purpose now.")
        n = session_end.record_retirements([{"role": "user", "text": text}],
                                           session="s", out_path=p)
        assert n == 1, f"expected 1 row, got {n!r}"
        assert _rows_of(p)[0]["paths"] == ["chat/export/routing.csv", "chat/export/lookup.csv"], \
            f"paths: {_rows_of(p)[0]['paths']!r}"


def test_a_long_quote_is_recorded_whole_not_sliced():
    # never-truncate-a-record: quote is a RECORDED value. A [:1400]-style slice on a
    # long turn makes a truncated record that looks complete.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "retirements.jsonl"
        text = ("context " * 200) + " " + CSV_SENTENCE + " " + ("trailing " * 200)
        assert len(text) > 1400, f"fixture too short to prove no slicing: {len(text)}"
        n = session_end.record_retirements([{"role": "user", "text": text}],
                                           session="s", out_path=p)
        assert n == 1, f"expected 1 row, got {n!r}"
        got = _rows_of(p)[0]["quote"]
        assert got == text, (f"quote was sliced: {len(got)} of {len(text)} characters "
                             f"recorded; a truncated record reads as a complete one")


def test_ordinary_prose_is_not_a_retirement():
    # NEGATIVE assertion. Every one of these shares a word with the lexicon ('delete',
    # 'csv', 'MCP') without retiring anything. An implementation that greps for a bare
    # word fills the ledger with noise, and a noisy ledger is dismissed within a week.
    NEGATIVES = [
        "I added CSV export to the report and committed it.",
        "The tests pass; I'll delete the scratch file later.",
        "Deleted the stale branch, nothing else touched.",
        "The MCP integration is working now.",
    ]
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "retirements.jsonl"
        for text in NEGATIVES:
            n = session_end.record_retirements([{"role": "assistant", "text": text}],
                                               session="s", out_path=p)
            assert n == 0, f"over-matched {text!r} -> {n} row(s)"
        assert not p.exists(), f"no retirement anywhere must create no file: {p}"


def test_creates_a_missing_parent_directory():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sub" / "deep" / "retirements.jsonl"
        n = session_end.record_retirements([{"role": "user", "text": CSV_SENTENCE}],
                                           session="s", out_path=p)
        assert n == 1, f"expected 1 row, got {n!r}"
        assert p.is_file(), f"the ledger's parent dirs must be created: {p}"


def test_announces_the_ledger_and_the_count():
    # The close prints what it wrote, in its own voice: a ledger written silently is one
    # nobody believes, and a row nobody can see is a record that does not exist.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "retirements.jsonl"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            session_end.record_retirements([{"role": "user", "text": CSV_SENTENCE}],
                                           session="s", out_path=p)
        out = buf.getvalue()
        assert "retirements.jsonl" in out, f"must name the ledger: {out!r}"
        assert re.search(r"1 retirement", out), \
            f"must give the count as a count of retirements: {out!r}"


def test_malformed_turns_do_not_break_the_close():
    # session_end.py is the only thing guaranteed to be satisfied. A close-out that dies
    # on a turn missing its role, or carrying a non-string text, is worse than no ledger.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "retirements.jsonl"
        n = session_end.record_retirements([{"role": "user"}, {}, {"text": 42},
                                            {"role": "user", "text": None}],
                                           session="s", out_path=p)
        assert n == 0, f"malformed turns must yield 0 rows, got {n!r}"
        assert not p.exists(), f"nothing retired must create no file: {p}"


TESTS = [
    test_writes_the_verbatim_retirement_row_to_the_path_it_is_given,
    test_writes_no_file_and_returns_zero_when_nothing_retires,
    test_records_both_voices_under_their_own_actor,
    test_paths_named_in_the_sentence_are_recorded_verbatim,
    test_a_long_quote_is_recorded_whole_not_sliced,
    test_ordinary_prose_is_not_a_retirement,
    test_creates_a_missing_parent_directory,
    test_announces_the_ledger_and_the_count,
    test_malformed_turns_do_not_break_the_close,
]

if __name__ == "__main__":
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"{len(TESTS) - failed}/{len(TESTS)} passed")
    sys.exit(1 if failed else 0)
