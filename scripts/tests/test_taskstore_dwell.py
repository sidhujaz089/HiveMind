#!/usr/bin/env python3
"""t900: taskstore records when a task was minted, first acted on, and closed.

The class this pins (RCA workspace/rca/0bd54c74cb5c.md, DE-10 / TH-18): a fix named in the
queue sits unbuilt and nothing records how long it sat. t900 is its own specimen -- minted
2026-10-08T080709Z, still queued on 2026-10-10, with no record of that dwell anywhere.

RED ON MAIN because projects/app-repo/taskstore.py's add() writes no minted_ts, and its
set_status() writes no first_action_ts and no closed_ts, so every lookup below finds no such
key and the first check in every test fires. It is NOT red for a bug of its own: it imports
taskstore from the path it really lives on, needs no pytest, spawns no subprocess, and writes
only inside a tempdir.

Run: python3 scripts/tests/test_taskstore_dwell.py
"""
import importlib.util
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]              # scripts/tests -> repo root
TS_PATH = ROOT / "projects" / "app-repo" / "taskstore.py"
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}Z$")       # taskstore.now()'s own format

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("FAIL: " + msg)


def load_taskstore(tmp):
    """Import taskstore from where it lives, then point its whole world at tmp.

    taskstore computes TASKS/DONE/QUEUE/LOCK/ATTACH at import time from its own file
    location (lines 40-45), so a test that only imports it writes into the live queue.
    Every one of those names is repointed before anything is written.
    """
    spec = importlib.util.spec_from_file_location("ts_under_test", TS_PATH)
    ts = importlib.util.module_from_spec(spec)
    sys.modules["ts_under_test"] = ts
    spec.loader.exec_module(ts)
    ts.TASKS = tmp / "tasks"
    ts.DONE = ts.TASKS / "done"
    ts.QUEUE = ts.TASKS / "QUEUE.md"
    ts.LOCK = ts.TASKS / ".lock"
    ts.ATTACH = tmp / "attachments"
    ts.TASKS.mkdir(parents=True, exist_ok=True)
    ts.DONE.mkdir(parents=True, exist_ok=True)
    ts.ATTACH.mkdir(parents=True, exist_ok=True)
    ts.QUEUE.write_text("")
    ts._resolved_path = lambda: tmp / "RESOLVED.md"      # set_status() writes it (line 661)
    return ts


def read_meta(ts, tid):
    """A task's front-matter dict, found in the open queue AND in done/ -- a task that
    left the queue is no longer in the directory a flat listing sees, which is the exact
    blindness the RCA this came from is about."""
    meta, _ = ts.find(tid)
    if meta:
        return meta
    for d in (ts.TASKS, ts.DONE):
        for f in sorted(Path(d).glob(tid + "--*.md")):
            return ts.parse(f)[0]
    return {}


def front_text(ts, tid):
    """The raw front-matter TEXT of the file. A field that _rewrite drops on the next
    round-trip must not get to hide inside a dict built from the file."""
    meta = read_meta(ts, tid)
    assert meta, "no task file on disk for " + str(tid)
    parts = Path(str(meta["path"])).read_text(encoding="utf-8").split("---")
    return parts[1] if len(parts) >= 3 else ""


def test_mint_stamps_minted_ts_and_only_minted_ts():
    with tempfile.TemporaryDirectory() as d:
        ts = load_taskstore(Path(d))
        tid = ts.add("dwell probe", area="workspace", source="session-rca")
        meta = read_meta(ts, tid)
        check(bool(meta), "add() returned " + str(tid) + " but no task file can be read back")
        v = meta.get("minted_ts")
        check(v is not None,
              "add() records no minted_ts: the moment a task was minted is unrecorded")
        check(bool(v) and bool(STAMP.match(str(v))),
              "minted_ts " + repr(v) + " is not a taskstore timestamp (YYYY-MM-DDTHHMMSSZ)")
        check(not meta.get("first_action_ts"),
              "a task minted seconds ago has had no action: first_action_ts must be ABSENT at mint")
        check(not meta.get("closed_ts"),
              "a task minted seconds ago is not closed: closed_ts must be ABSENT at mint")


def test_first_action_stamped_on_first_move_off_queued():
    with tempfile.TemporaryDirectory() as d:
        ts = load_taskstore(Path(d))
        tid = ts.add("dwell probe", area="workspace", source="session-rca")
        minted = read_meta(ts, tid).get("minted_ts")
        check(ts.set_status(tid, "doing") is True, "set_status(tid, 'doing') did not return True")
        meta = read_meta(ts, tid)
        fa = meta.get("first_action_ts")
        check(bool(fa) and bool(STAMP.match(str(fa))),
              "the first move off queued recorded no first_action_ts: " + repr(fa))
        check(meta.get("minted_ts") == minted,
              "set_status moved minted_ts; the mint stamp must never change")
        check(not meta.get("closed_ts"), "doing is not a close: closed_ts must be absent")
        check(ts.set_status(tid, "queued") is True, "set_status(tid, 'queued') did not return True")
        check(not read_meta(ts, tid).get("closed_ts"), "returning to queued stamped a close")
        ts.set_status(tid, "blocked")
        check(read_meta(ts, tid).get("first_action_ts") == fa,
              "a later status change overwrote first_action_ts; it is the FIRST action, not the latest")


def test_close_stamps_closed_ts_and_keeps_the_earlier_ones():
    with tempfile.TemporaryDirectory() as d:
        ts = load_taskstore(Path(d))
        tid = ts.add("dwell probe", area="workspace", source="session-rca")
        minted = read_meta(ts, tid).get("minted_ts")
        ts.set_status(tid, "doing")
        first = read_meta(ts, tid).get("first_action_ts")
        check(ts.set_status(tid, "done") is True, "set_status(tid, 'done') did not return True")
        meta = read_meta(ts, tid)
        check(bool(meta.get("closed_ts")), "set_status(tid, 'done') recorded no closed_ts")
        check(meta.get("first_action_ts") == first, "closing overwrote first_action_ts")
        check(meta.get("minted_ts") == minted, "closing overwrote minted_ts")
        fm = front_text(ts, tid)
        for key in ("minted_ts", "first_action_ts", "closed_ts"):
            check(key + ":" in fm,
                  key + " is not written into the task file's front matter, so the next "
                  "appended note silently deletes it (_rewrite's key list, line 444)")


def test_cancel_is_a_close_too():
    with tempfile.TemporaryDirectory() as d:
        ts = load_taskstore(Path(d))
        tid = ts.add("dwell probe", area="workspace", source="session-rca")
        check(ts.set_status(tid, "cancelled") is True, "set_status(tid, 'cancelled') failed")
        meta = read_meta(ts, tid)
        check(str(meta.get("status")) == "cancelled",
              "the task's status is not cancelled: " + repr(meta.get("status")))
        check(bool(meta.get("closed_ts")),
              "cancelled leaves the queue (LEAVES_QUEUE) exactly as done does, so it must stamp closed_ts")


def test_unknown_status_is_refused_and_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        ts = load_taskstore(Path(d))
        tid = ts.add("dwell probe", area="workspace", source="session-rca")
        minted = read_meta(ts, tid).get("minted_ts")
        check(ts.set_status(tid, "closed") is False, "a status outside STATUSES must return False")
        meta = read_meta(ts, tid)
        check(str(meta.get("status")) == "queued", "an invalid status changed the status")
        check(meta.get("minted_ts") == minted, "an invalid status minted a stamp")
        check(not meta.get("first_action_ts"), "an invalid status stamped first_action_ts")
        check(not meta.get("closed_ts"), "an invalid status stamped closed_ts")


TESTS = (test_mint_stamps_minted_ts_and_only_minted_ts,
         test_first_action_stamped_on_first_move_off_queued,
         test_close_stamps_closed_ts_and_keeps_the_earlier_ones,
         test_cancel_is_a_close_too,
         test_unknown_status_is_refused_and_writes_nothing)

if __name__ == "__main__":
    for fn in TESTS:
        try:
            fn()
        except Exception as e:                              # noqa: BLE001
            FAILS.append(fn.__name__ + " raised " + type(e).__name__ + ": " + str(e))
            print("FAIL: " + fn.__name__ + " raised " + type(e).__name__ + ": " + str(e))
    print("\n" + str(len(FAILS)) + " failure(s) in test_taskstore_dwell.py")
    sys.exit(1 if FAILS else 0)
