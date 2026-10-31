#!/usr/bin/env python3
"""t1051: a review's `ended` date is stamped from the session's LAST TEXT TURN at enqueue, and a
review whose stored ended_epoch postdates that stamp is REFUSED before any model call.

Red on main for the missing change. scripts/session_review.py stamps ended_epoch from the transcript
file's mtime (line 147), turns() (line 64) returns only (role, text) and drops every row timestamp,
and the module has no check_ended and no EndedMismatch. The recorded defect, workspace/rca/
73eab61098c7.md: a session whose last text turn is 2026-09-02T01:59Z carries ended_epoch
1790134400.981 = 2026-09-23T03:33Z, 21 days of drift, and that date is what the EXTRACT prompt and
the commit cover search read -- a window that opens 21 days after the event it should cover.

Runs with plain python3 (pytest is NOT installed). Temp dirs and patched module globals only: no live
state, no model calls, no process left running.
"""
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import session_review as sr  # noqa: E402
import skills  # noqa: E402

# The skill loader APPENDS to workspace/skill-loads.jsonl (scripts/skills.py line 213 and 304-311)
# every time extract() builds a prompt: scripts/session_review.py imports skills inside its part loop
# (line 439) and calls skills.with_skills (line 443), and with_skills logs by default. extract()
# must be exercised here to prove it refuses before the model, so the log goes to a scratch file --
# the standing pattern in scripts/tests/test_session_review.py. Never the live workspace.
skills.LOADS = Path(tempfile.mkdtemp(prefix="skill-loads-")) / "skill-loads.jsonl"

DAY = 86400
FIRST_ISO = "2026-09-01T17:01:54.000Z"
LAST_ISO = "2026-09-02T01:59:44.000Z"      # the session's last TEXT turn (the RCA's own date)
LATE_ISO = "2026-09-23T03:33:20.000Z"      # when the transcript FILE was next touched: +21 days
LATE_EPOCH = 1790134400.981                # the real ended_epoch recorded in that RCA


def epoch(value):
    """ISO-8601 with a Z suffix, as Claude Code rows carry it."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


FIRST_EPOCH = epoch(FIRST_ISO)
LAST_EPOCH = epoch(LAST_ISO)

# Real Claude Code row shapes. Two rows carry the LATE timestamp and are NOT text turns -- an
# assistant row whose text is whitespace, and a summary row -- so the stamp must be the last TEXT
# turn's timestamp, not the last row's and not the file's mtime.
ROWS = [
    {"type": "user", "timestamp": FIRST_ISO, "message": {"content": "start phase 2"}},
    {"type": "assistant", "timestamp": LAST_ISO,
     "message": {"content": [{"type": "text", "text": "Phase 2 running."}]}},
    {"type": "assistant", "timestamp": LATE_ISO,
     "message": {"content": [{"type": "text", "text": "   "}]}},
    {"type": "summary", "timestamp": LATE_ISO, "summary": "duo phase 2"},
]


def sandbox(tmp):
    """Re-point every path session_review writes at tmp and capture its ledger rows, so a run of
    this suite cannot touch workspace/session-reviews/. Returns (base, logged_rows)."""
    base = tmp / "sr"
    for sub in ("pending", "done", "failed", "findings"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    sr.BASE = base
    sr.PENDING = base / "pending"
    sr.DONE = base / "done"
    sr.FAILED = base / "failed"
    sr.FINDINGS = base / "findings"
    sr.LEDGER = base / "ledger.jsonl"
    log = []
    sr._log = lambda row: log.append(row)
    return base, log


def write_transcript(base, rows=None):
    rows = ROWS if rows is None else rows
    path = base / "transcript.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def test_turns_public_shape_unchanged(tmp):
    """The contract the change must not break: turns() keeps returning (role, text) pairs. Dozens
    of callers count its length, and workspace/rca/73eab61098c7.md relies on len(turns(...))."""
    base, _log = sandbox(tmp)
    path = write_transcript(base)
    got = sr.turns("claude", path)
    assert got == [("user", "start phase 2"), ("assistant", "Phase 2 running.")], \
        f"turns() no longer returns (role, text) pairs: {got!r}"


def test_turns_can_carry_the_row_timestamp(tmp):
    """The date must come from the TEXT rows' own timestamp fields -- not the file's mtime, and
    not the two late rows that carry no text."""
    base, _log = sandbox(tmp)
    path = write_transcript(base)
    rows = sr.turns("claude", path, with_ts=True)
    assert [r[:2] for r in rows] == [("user", "start phase 2"), ("assistant", "Phase 2 running.")], \
        f"with_ts=True must not change the roles or the texts: {rows!r}"
    assert [r[2] for r in rows] == [FIRST_EPOCH, LAST_EPOCH], \
        f"timestamps must be the text rows' own, in order: {rows!r}"


def test_enqueue_stamps_the_last_text_turn(tmp):
    """The defect: ended_epoch came from the file's mtime, 21 days after the last text turn."""
    base, log = sandbox(tmp)
    path = write_transcript(base)
    os.utime(path, (path.stat().st_atime, epoch(LATE_ISO) + DAY))   # the file trails its own session
    mtime = path.stat().st_mtime
    name = sr.enqueue("claude", "aeef1f57", path)
    assert name, "enqueue() queued nothing: the fixture yielded no text turns"
    req = json.loads((sr.PENDING / name).read_text(encoding="utf-8"))
    assert "ended_epoch" in req, "enqueue wrote no ended_epoch"
    assert abs(req["ended_epoch"] - LAST_EPOCH) <= 2, \
        f"ended_epoch {req['ended_epoch']!r} is not the last text turn ({LAST_EPOCH})"
    assert abs(req["ended_epoch"] - mtime) > DAY, \
        "ended_epoch is still the transcript file's mtime -- the defect this task exists for"
    assert req.get("ended_source") == "last-text-turn", f"ended_source: {req.get('ended_source')!r}"
    assert "last_text_turn_ts" in req, "enqueue wrote no last_text_turn_ts"
    assert abs(float(req["last_text_turn_ts"]) - LAST_EPOCH) <= 2, req["last_text_turn_ts"]
    assert [r for r in log if r.get("event") == "queued"], f"the queued ledger row is gone: {log!r}"


def test_no_usable_timestamp_falls_back_to_mtime(tmp):
    """The honest fallback: rows with no timestamp are stamped from the file and are NEVER refused
    for a date the transcript does not carry -- so the guard cannot block the whole queue. These
    are the real row shapes in scripts/tests/test_session_review.py, which carry no timestamp."""
    base, _log = sandbox(tmp)
    path = write_transcript(base, [
        {"type": "user", "message": {"content": "a"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "b"}]}}])
    name = sr.enqueue("claude", "sess", path)
    assert name, "enqueue() queued nothing"
    req = json.loads((sr.PENDING / name).read_text(encoding="utf-8"))
    assert req.get("ended_source") == "file-mtime", f"ended_source: {req.get('ended_source')!r}"
    assert abs(req["ended_epoch"] - path.stat().st_mtime) <= 2, req["ended_epoch"]


def test_check_ended_names_the_delta(tmp):
    """The computed check: refuse when the stored date postdates the stamp, name the gap in days,
    and stay silent for a consistent record, an early one, and a legacy one with no stamp."""
    stamps = [FIRST_EPOCH, LAST_EPOCH, None, None]
    late = {"id": "aeef1f57", "ended_epoch": LAST_EPOCH + 21 * DAY, "last_text_turn_ts": LAST_EPOCH}
    msg = sr.check_ended(late, stamps)
    assert msg, "a stored ended date 21 days after the last text turn was not refused"
    assert "21" in msg, f"the refusal must name the gap in days: {msg!r}"
    assert "day" in msg.lower(), f"the refusal must name the gap in days: {msg!r}"
    assert "aeef1f57" in msg, f"the refusal must say which session it refused: {msg!r}"
    same = {"id": "s", "ended_epoch": LAST_EPOCH, "last_text_turn_ts": LAST_EPOCH}
    assert sr.check_ended(same, stamps) == "", "a record stamped from its own last text turn was refused"
    early = {"id": "s", "ended_epoch": LAST_EPOCH - DAY, "last_text_turn_ts": LAST_EPOCH}
    assert sr.check_ended(early, stamps) == "", "a record ending BEFORE its last text turn was refused"
    legacy = {"id": "y", "ended_epoch": epoch(LATE_ISO)}
    assert sr.check_ended(legacy, [None]) == "", "a record with no stamp must not be blocked"
    assert sr.check_ended(legacy, []) == "", "an all-empty stamp list must not read as a refusal"


def test_extract_refuses_before_any_model_call(tmp):
    """The refusal sits on the review path, not in a helper: zero model calls, no ledger row, the
    request left where it was, and nothing filed as done."""
    base, log = sandbox(tmp)
    path = write_transcript(base)
    req = {"harness": "claude", "id": "aeef1f57", "path": str(path), "queued": 1790000000,
           "from_turn": 0, "to_turn": len(ROWS), "ended_source": "last-text-turn",
           "ended_epoch": LAST_EPOCH + 21 * DAY, "last_text_turn_ts": LAST_EPOCH}
    rf = sr.PENDING / "claude-aeef1f57.json"
    rf.write_text(json.dumps(req, indent=1) + "\n", encoding="utf-8")

    calls = []

    def recorder(prompt, purpose, **kw):
        calls.append(purpose)
        return "[]"

    raised = None
    try:
        sr.extract(rf, asker=recorder)
    except sr.EndedMismatch as exc:
        raised = exc
    assert raised is not None, \
        "extract() ran a review whose ended date postdates its last text turn"
    assert "21" in str(raised), f"the refusal must name the gap in days: {raised!r}"
    assert calls == [], f"the model was asked {len(calls)} time(s) before the refusal"
    assert rf.is_file(), "the refusal deleted or moved the request: a refused review stays for a person"
    assert not (sr.DONE / "claude-aeef1f57.json").is_file(), "a refused review was filed as done"
    assert not [r for r in log if r.get("event") == "reviewed"], f"a refusal logged as reviewed: {log!r}"


TESTS = [(name, fn) for name, fn in sorted(globals().items())
         if name.startswith("test_") and callable(fn)]


def main():
    failed = 0
    for name, fn in TESTS:
        tmp = Path(tempfile.mkdtemp(prefix="t1051-"))
        try:
            fn(tmp)
            print(f"ok   {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(TESTS) - failed}/{len(TESTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
