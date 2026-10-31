#!/usr/bin/env python3
"""Oracle for t617: count a session review's timeout burn from the logs its record names.

RCA e9a2e94ec33f filed this into t617: the review record
workspace/session-reviews/done/claude-1ff24a44-5a10-4ff2-8e85-5a2b7b415764.json:12 quotes
"9 earlier run(s) ended in a TimeoutExpired past 6000 s" and names nine dispatch log files, and
nothing on disk turns those names into per-attempt rows -- so the cost of an unusable review is
QUOTED from a log line, never counted. This suite pins the counter.

Red on main for the missing change and for nothing else: session_review has no count_burn,
no total_seconds and no `burn` subcommand, so every test prints FAIL (AttributeError, argparse
"invalid choice") and the file exits 1. It imports session_review, which DOES exist, so it runs
to completion on main -- it is red, it does not crash.

Run: python3 scripts/tests/test_review_burn.py
"""
import json
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import session_review as sr  # noqa: E402  exists; the functions it lacks are the change

# The head and tail of a real timeout log: workspace/run-logs/dispatch/
# claude-1ff24a44-5a10-4ff2-8e85-5a2b7b415764.json--session-review--qwen3.8_27b-mtp-q4_K_M--1790660435.log
TIMEOUT_6000 = (
    "[2026-09-29T05:40:36Z] /usr/bin/python3 /home/user/ai-workspace/scripts/session_review.py "
    "extract --request /home/user/ai-workspace/workspace/session-reviews/pending/"
    "claude-1ff24a44-5a10-4ff2-8e85-5a2b7b415764.json\n"
    "Traceback (most recent call last):\n"
    "  File \"/home/user/ai-workspace/scripts/session_review.py\", line 202, in ask\n"
    "    cp = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True, timeout=6000)\n"
    "subprocess.TimeoutExpired: Command '['/usr/bin/python3', 'ask_q16.py']' "
    "timed out after 6000 seconds\n"
)
TIMEOUT_9000 = TIMEOUT_6000.replace("timeout=6000", "timeout=9000").replace(
    "after 6000 seconds", "after 9000 seconds")
KILLED = (
    "[2026-09-30T14:55:00Z] /usr/bin/python3 /home/user/ai-workspace/scripts/session_review.py "
    "extract --request /home/user/ai-workspace/workspace/session-reviews/pending/"
    "claude-1ff24a44-5a10-4ff2-8e85-5a2b7b415764.json\n"
    "Killed\n"
)
PRE = "claude-1ff24a44-5a10-4ff2-8e85-5a2b7b415764.json--session-review--qwen3.8_27b-mtp-q4_K_M--"
A, B, C = PRE + "1790660435.log", PRE + "1790671567.log", PRE + "1790791276.log"
UNNAMED = PRE + "9999999999.log"
MISSING = PRE + "8888888888.log"


def _fixture(d):
    """Three logs the record names (6000 s, 9000 s, killed) plus one sitting in the directory
    that the record does NOT name. Real directory holds 13 such logs; the record names 9 --
    the counter reads the record, it does not glob."""
    (d / A).write_text(TIMEOUT_6000, encoding="utf-8")
    (d / B).write_text(TIMEOUT_9000, encoding="utf-8")
    (d / C).write_text(KILLED, encoding="utf-8")
    (d / UNNAMED).write_text(TIMEOUT_6000, encoding="utf-8")
    return {"harness": "claude",
            "id": "1ff24a44-5a10-4ff2-8e85-5a2b7b415764",
            "unusable": [{"at": "2026-09-30T18:08:33Z",
                          "why": "history recorded 2026-09-30: 9 earlier run(s) ended in a "
                                 "TimeoutExpired past 6000 s, before attempts were counted -- "
                                 + ", ".join([A, B, C])}]}


def test_one_row_per_named_log_and_nothing_else():
    with tempfile.TemporaryDirectory(prefix="t617-") as t:
        d = Path(t)
        rows = sr.count_burn(_fixture(d), d)
        assert len(rows) == 3, "one row per NAMED log: got %d %r" % (len(rows), rows)
        got = sorted(str(r["log"]) for r in rows)
        assert got == sorted([A, B, C]), "rows carry the named logs' own names, got %r" % (got,)
        assert UNNAMED not in got, "a log on disk the record does not name must NOT be counted"


def test_each_row_carries_that_log_s_own_duration_and_kill_reason():
    with tempfile.TemporaryDirectory(prefix="t617-") as t:
        d = Path(t)
        rows = {str(r["log"]): r for r in sr.count_burn(_fixture(d), d)}
        assert rows[A]["duration_seconds"] == 6000, rows[A]
        assert rows[B]["duration_seconds"] == 9000, rows[B]
        assert rows[C]["duration_seconds"] == 0, rows[C]
        assert isinstance(rows[B]["duration_seconds"], int), "seconds are a number, not a string"
        assert rows[A]["kill_reason"] == "timeout", rows[A]
        assert rows[C]["kill_reason"] == "signalled", rows[C]
        assert rows[B]["found"] is True, rows[B]


def test_a_named_log_with_no_file_is_a_counted_gap_not_a_silence():
    """A named log absent from disk is a row that says so: never dropped, never a silent zero."""
    with tempfile.TemporaryDirectory(prefix="t617-") as t:
        d = Path(t)
        rec = _fixture(d)
        rec["unusable"][0]["why"] += ", " + MISSING
        rows = {str(r["log"]): r for r in sr.count_burn(rec, d)}
        assert len(rows) == 4, "the missing log still gets a row: %r" % (sorted(rows),)
        assert rows[MISSING]["found"] is False, rows[MISSING]
        assert rows[MISSING]["kill_reason"] == "missing", rows[MISSING]
        assert rows[MISSING]["duration_seconds"] == 0, rows[MISSING]
        assert rows[A]["found"] is True, rows[A]


def test_total_seconds_is_the_sum_of_the_rows():
    with tempfile.TemporaryDirectory(prefix="t617-") as t:
        d = Path(t)
        rows = sr.count_burn(_fixture(d), d)
        assert sr.total_seconds(rows) == 6000 + 9000 + 0, sr.total_seconds(rows)


def test_nine_6000s_attempts_count_as_15_hours():
    """The claim on t617's plate: 9 x 6000 s = 54000 s = 15 h. Counted from logs, not quoted."""
    with tempfile.TemporaryDirectory(prefix="t617-") as t:
        d = Path(t)
        names = []
        for i in range(9):
            n = PRE + "17906604%02d.log" % i
            (d / n).write_text(TIMEOUT_6000, encoding="utf-8")
            names.append(n)
        rec = {"unusable": [{"at": "2026-09-30T18:08:33Z", "why": "-- " + ", ".join(names)}]}
        rows = sr.count_burn(rec, d)
        assert len(rows) == 9, "expected 9 rows, got %d" % len(rows)
        total = sr.total_seconds(rows)
        assert total == 54000, total
        assert total / 3600.0 == 15.0, total


def test_cli_prints_one_json_row_per_attempt_and_writes_no_live_state():
    before = sr.LEDGER.stat().st_size if sr.LEDGER.exists() else 0
    with tempfile.TemporaryDirectory(prefix="t617-") as t:
        d = Path(t)
        rec = d / "req.json"
        rec.write_text(json.dumps(_fixture(d)), encoding="utf-8")
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "session_review.py"), "burn",
                              "--record", str(rec), "--log-dir", str(d)],
                             capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        assert out.returncode == 0, (out.returncode, out.stdout[:400], out.stderr[:400])
        lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
        assert len(lines) == 3, "one JSON row per attempt, got %r" % (out.stdout[:600],)
        got = [json.loads(ln) for ln in lines]
        assert sorted(g["duration_seconds"] for g in got) == [0, 6000, 9000], got
        assert sorted(g["kill_reason"] for g in got) == ["signalled", "timeout", "timeout"], got
        assert all(g["found"] is True for g in got), got
        assert sorted(str(g["log"]) for g in got) == sorted([A, B, C]), got
    after = sr.LEDGER.stat().st_size if sr.LEDGER.exists() else 0
    assert after == before, "the burn path must touch no live state: ledger grew by %d bytes" % (after - before)


if __name__ == "__main__":
    _tests = sorted(n for n in list(globals()) if n.startswith("test_"))
    _fails = []
    for _name in _tests:
        try:
            globals()[_name]()
            print("PASS " + _name)
        except Exception:
            _fails.append(_name)
            print("FAIL " + _name)
            traceback.print_exc()
    print("%d test(s), %d failed" % (len(_tests), len(_fails)))
    sys.exit(1 if _fails else 0)
