#!/usr/bin/env python3
"""t1035 (a): every dispatch recorded as started owes exactly one terminal row.

RCA 4027ae420d25 (workspace/rca/4027ae420d25.md, D3): the dispatch that ran THAT RCA left a
`started` row in the dispatch ledger and no row saying it ended, and its run log was a header
with nothing under it -- the shape docs/skills/never-truncate-a-record names. Today the only
thing that closes such a row is dispatch_loop.account_lost, which needs the pid to be dead AND
the start to be older than LOST_AFTER_S, and whose row points at no result file.

This suite pins the ledger's accounting over row dicts in memory. It spawns no dispatch, opens
no live ledger, calls no model, writes no file, reads no file, and leaves nothing running.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import dispatch_loop  # noqa: E402  the module this task extends

# Real artefacts of the dispatch the RCA found orphaned, and a second fully distinct dispatch,
# so the suite can tell one dispatch from another. Values, not placeholders.
RCA_KEY = "4027ae420d25"
RCA_PID = 170187
RCA_LOG = ("workspace/run-logs/dispatch/"
           "4027ae420d25.json--rca--swift--1791693140.log")
RCA_START = {
    "event": "started", "kind": "rca", "chunk": "4027ae420d25.json",
    "model": "swift", "key": RCA_KEY, "pid": RCA_PID,
    "brief": None, "brief_sha256": None, "log": RCA_LOG,
    "at": "2026-10-11T04:32:20Z",
}
OTHER_KEY = "t999"
OTHER_PID = 4021
OTHER_LOG = "workspace/run-logs/dispatch/t999.md--production--gemma426b--4021.log"
OTHER_START = {
    "event": "started", "kind": "production", "chunk": "t999.md",
    "model": "gemma4:26b", "key": OTHER_KEY, "pid": OTHER_PID,
    "brief": "t999.md", "brief_sha256": "ab12cd34", "log": OTHER_LOG,
    "at": "2026-10-11T05:00:00Z",
}


def test_terminal_row_carries_the_five_fields_the_rca_named():
    """D3 asked for {key, pid, exit, seconds, result_path}. All five, with real values."""
    row = dispatch_loop.terminal_row(RCA_START, exit_code=-9, seconds=3724.5,
                                     result_path=RCA_LOG)
    assert row["event"] == "finished", row.get("event")
    assert row["key"] == RCA_KEY, row.get("key")
    assert row["pid"] == RCA_PID, row.get("pid")
    assert row["exit"] == -9, row.get("exit")
    assert row["seconds"] == 3724.5, row.get("seconds")
    assert row["result_path"] == RCA_LOG, row.get("result_path")
    assert isinstance(row.get("at"), str) and row["at"], (
        "a terminal row that names no time cannot be ordered against its start: "
        f"{row.get('at')!r}")
    # -9 is in YIELD_EXITS (line 798): failures()/futile()/parked() skip a yield, so the row
    # must read as a stand-down to them -- not a failure, and not an accepted delivery.
    assert row["yielded"] is True, row.get("yielded")
    assert row["accepted"] is False, row.get("accepted")


def test_terminal_row_keeps_what_todays_readers_read_off_a_finished_row():
    """A finished row is read by failures()/futile()/refused_on()/parked()/lane_status.py.
    Every field they read off one today must still be on a row built by terminal_row."""
    row = dispatch_loop.terminal_row(OTHER_START, exit_code=0, seconds=12.5,
                                     result_path=OTHER_LOG, at="2026-10-11T05:02:12Z")
    assert row["kind"] == "production", row.get("kind")
    assert row["chunk"] == "t999.md", row.get("chunk")
    assert row["model"] == "gemma4:26b", row.get("model")
    assert row["brief"] == "t999.md", row.get("brief")
    assert row["brief_sha256"] == "ab12cd34", row.get("brief_sha256")
    assert row["key"] == OTHER_KEY, row.get("key")
    assert row["pid"] == OTHER_PID, row.get("pid")
    assert row["exit"] == 0, row.get("exit")
    assert row["seconds"] == 12.5, row.get("seconds")
    assert row["accepted"] is True, row.get("accepted")
    assert row["yielded"] is False, row.get("yielded")
    assert row["at"] == "2026-10-11T05:02:12Z", row.get("at")
    assert json.loads(json.dumps(row)) == row, "a ledger row must survive a json round-trip"


def test_result_path_is_recorded_whole_never_a_slice():
    """docs/skills/never-truncate-a-record: a recorded value is never cut. No cap, no ellipsis."""
    long_log = ("workspace/run-logs/dispatch/"
                + "t1035--record-dispatch-terminal-rows/" * 4
                + "and-a-per-turn-effort-population-size-join.json--rca--swift--1791693140.log")
    assert len(long_log) > 200, len(long_log)
    row = dispatch_loop.terminal_row(dict(RCA_START, log=long_log), exit_code=0,
                                     seconds=1.0, result_path=long_log)
    assert row["result_path"] == long_log, row.get("result_path")
    assert not row["result_path"].endswith("..."), row["result_path"][-12:]
    assert len(row["result_path"]) == len(long_log), (
        f"recorded {len(row['result_path'])} of {len(long_log)} characters")


def test_record_terminal_appends_the_row_it_built():
    """The record half of the pair: append ONE row, and it is the row you return."""
    collected = []
    row = dispatch_loop.record_terminal(RCA_START, exit_code=0, seconds=12.5,
                                        result_path=RCA_LOG, record=collected.append)
    assert len(collected) == 1, f"expected exactly one appended row, got {len(collected)}"
    assert collected[0] == row, "the row returned is not the row appended"
    assert collected[0]["event"] == "finished", collected[0].get("event")
    assert collected[0]["key"] == RCA_KEY, collected[0].get("key")
    assert collected[0]["result_path"] == RCA_LOG, collected[0].get("result_path")


def test_record_terminal_is_append_only():
    """The ledger is append-only: a prior row is never rewritten to say 'ended'."""
    seeded = [dict(RCA_START)]
    row = dispatch_loop.record_terminal(OTHER_START, exit_code=0, seconds=1.0,
                                        result_path=OTHER_LOG, record=seeded.append)
    assert len(seeded) == 2, len(seeded)
    assert seeded[0] == RCA_START, "record_terminal rewrote the row it was given"
    assert seeded[-1] is row, "the terminal row must be the last entry"


def test_open_dispatches_is_empty_when_every_start_has_a_terminal_row():
    history = [
        dict(RCA_START),
        {"event": "finished", "key": RCA_KEY, "pid": RCA_PID, "exit": 0, "seconds": 4.0},
        dict(OTHER_START),
        {"event": "finished", "key": OTHER_KEY, "pid": OTHER_PID, "exit": 0, "seconds": 2.0},
    ]
    assert dispatch_loop.open_dispatches(history) == [], dispatch_loop.open_dispatches(history)


def test_open_dispatches_reports_the_start_with_no_terminal_row():
    """THE CASE THE FIX EXISTS FOR: a start whose dispatch died before it recorded anything."""
    history = [
        dict(RCA_START),
        dict(OTHER_START),
        {"event": "finished", "key": OTHER_KEY, "pid": OTHER_PID, "exit": 0, "seconds": 2.0},
    ]
    open_rows = dispatch_loop.open_dispatches(history)
    assert len(open_rows) == 1, f"expected one open dispatch, got {len(open_rows)}: {open_rows}"
    assert open_rows[0]["key"] == RCA_KEY, open_rows[0].get("key")
    assert open_rows[0]["pid"] == RCA_PID, open_rows[0].get("pid")


def test_a_terminal_row_for_another_pid_does_not_close_the_dispatch():
    """One key is dispatched more than once (a re-dispatch after a yield). Closing pid 5030
    must not silence pid 170187 under the same key, nor pid 170187 under a different key."""
    history = [
        dict(RCA_START),
        {"event": "finished", "key": RCA_KEY, "pid": 5030, "exit": 0, "seconds": 1.0},
        {"event": "finished", "key": "t500", "pid": RCA_PID, "exit": 0, "seconds": 1.0},
    ]
    open_rows = dispatch_loop.open_dispatches(history)
    assert len(open_rows) == 1, f"expected one open dispatch, got {len(open_rows)}: {open_rows}"
    assert open_rows[0]["key"] == RCA_KEY, open_rows[0].get("key")
    assert open_rows[0]["pid"] == RCA_PID, open_rows[0].get("pid")


def test_open_dispatches_reports_nothing_for_rows_that_are_not_starts():
    """A `finished` row is not an obligation, and a tick row is not a dispatch."""
    history = [
        {"event": "tick", "kind": "tick", "key": None, "pid": None},
        {"event": "finished", "key": "t600", "pid": 707, "exit": 0, "seconds": 3.0},
        {"event": "lost", "key": "t601", "pid": 708, "exit": None, "seconds": None},
    ]
    assert dispatch_loop.open_dispatches(history) == [], dispatch_loop.open_dispatches(history)


if __name__ == "__main__":
    _names = sorted(n for n, f in list(globals().items())
                    if n.startswith("test_") and callable(f))
    _failed = 0
    for _name in _names:
        try:
            globals()[_name]()
            print(f"ok   {_name}")
        except Exception as _exc:
            _failed += 1
            print(f"FAIL {_name}: {type(_exc).__name__}: {_exc}")
    print(f"{len(_names) - _failed}/{len(_names)} passed")
    sys.exit(1 if _failed else 0)
