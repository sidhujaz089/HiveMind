#!/usr/bin/env python3
"""t695: a compaction must leave a row the next session can ask about.

DE-8 of workspace/rca/0bd54c74cb5c.md: "Whether compaction truncated this session's context
before row 5029 cannot be asserted or refuted -- no collector exists at that endpoint." The
record is prescribed by the task note: append ts, session, tokens_before, tokens_after,
boundary_turn to a compaction ledger, with a fixture that fails when a compacted session has
no row. dsh_usage.py is the harness's own reader of a session's stream, so the ledger belongs
beside it: the question is asked of the same files it reads.

Everything is written under a TemporaryDirectory: no live ledger, no ~/.dsh, no model,
no decompression of a real session file.
"""
import contextlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

FIELDS = ("ts", "session", "tokens_before", "tokens_after", "boundary_turn")
SID = "745a17ba-a559-4e99-81f6-3f8ec745ad46"
BEFORE = 997_646         # the peak before compaction, from context_watch.py's own docstring
AFTER = 60_412           # and immediately after it: two DISTINCT numbers, so one constant
BOUNDARY = 5029          # written into every row cannot pass
OTHER = "23a856ff"
OTHER_BEFORE = 191_000   # the 191K figure scripts/dsh_usage.py:56 names as the misread
STAMP = "2026-10-10T00:20:00Z"   # the date of the RCA that asks for this collector


def _parses(ts):
    try:
        datetime.fromisoformat(ts[:-1] + "+00:00")
    except ValueError:
        return False
    return True


def main():
    fails = []
    import tempfile
    import dsh_usage as du

    def ck(label, got, want):
        ok = got == want
        print(f"  [{'ok' if ok else 'BAD'}]    {label} -> {got!r}")
        if not ok:
            fails.append(f"{label}: expected {want!r}, got {got!r}")

    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "ledgers" / "compactions.jsonl"

        # The whole reason the collector exists: a session that compacted and left no row
        # must be ANSWERABLE as 'no row' -- not True, not a crash, not a file created.
        ck("a missing ledger answers: this session has no compaction row",
           du.has_compaction_row(ledger, SID), False)
        ck("asking a missing ledger must not create it", ledger.exists(), False)

        # One compaction writes one row carrying all five prescribed fields, with values.
        row = du.compaction_row(SID, BEFORE, AFTER, BOUNDARY)
        ck("the row carries exactly the five prescribed fields",
           sorted(row.keys()), sorted(FIELDS))
        ck("tokens_before is recorded whole", row.get("tokens_before"), BEFORE)
        ck("tokens_after is recorded whole", row.get("tokens_after"), AFTER)
        ck("boundary_turn is recorded whole", row.get("boundary_turn"), BOUNDARY)
        ck("the row names the session it happened to", row.get("session"), SID)
        ts = row.get("ts")
        ck("ts is stamped as a UTC ISO-8601 string",
           isinstance(ts, str) and ts.endswith("Z") and _parses(ts), True)

        # A back-fill carries the date of the EVENT, not the moment of writing.
        back = du.compaction_row(SID, BEFORE, AFTER, BOUNDARY, ts=STAMP)
        ck("an explicit ts is stored verbatim", back.get("ts"), STAMP)

        # Two compactions, two rows: append, never overwrite, never one row's values on
        # the other's session.
        du.append_ledger(ledger, row)
        du.append_ledger(ledger, du.compaction_row(OTHER, OTHER_BEFORE, AFTER, 44, ts=STAMP))
        lines = ledger.read_text(encoding="utf-8").splitlines()
        ck("two compactions append two rows", len(lines), 2)
        rows = [json.loads(x) for x in lines]
        ck("row 1 keeps its own session id", rows[0]["session"], SID)
        ck("row 2 keeps its own session id", rows[1]["session"], OTHER)
        ck("row 2 keeps its own tokens_before", rows[1]["tokens_before"], OTHER_BEFORE)
        ck("row 1 keeps its own tokens_before", rows[0]["tokens_before"], BEFORE)
        ck("the ledger is created where the caller named it, parents included",
           str(ledger.relative_to(Path(td))), "ledgers/compactions.jsonl")

        ck("a session with a row answers: yes", du.has_compaction_row(ledger, SID), True)
        ck("a second session with a row answers: yes", du.has_compaction_row(ledger, OTHER), True)
        ck("a session that never compacted answers: no",
           du.has_compaction_row(ledger, "session-never-compacted"), False)
        ck("a prefix of a recorded session id is not a match",
           du.has_compaction_row(ledger, SID[:8]), False)
        ck("an empty session id is not a match", du.has_compaction_row(ledger, ""), False)

        # The ledger is written while the session is live, so its LAST line can be torn.
        # The intact rows survive and the skip is REPORTED, not swallowed.
        torn = Path(td) / "torn.jsonl"
        du.append_ledger(torn, row)
        with torn.open("a", encoding="utf-8") as fh:
            fh.write('{"session": "s-torn", "tokens_b')
        err = io.StringIO()
        hit = None
        try:
            with contextlib.redirect_stderr(err):
                hit = du.has_compaction_row(torn, SID)
        except Exception as e:
            fails.append(f"a torn last line must be skipped, not raised: {e!r}")
        ck("the intact row survives a torn last line", hit, True)
        ck("the torn line is reported, not swallowed", "line 2" in err.getvalue(), True)

    for x in fails:
        print("FAIL:", x)
    print("test_dsh_usage_compaction:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
