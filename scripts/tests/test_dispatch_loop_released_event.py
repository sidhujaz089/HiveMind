#!/usr/bin/env python3
"""t1399: a release must be a LEDGER ROW, not a note in the brief.

RCA bb9f66ff7673, thread D2: "the stated recorder is a free-text dated note in the brief --
that is not a recorder ... No RELEASED ledger row is written." Today route_waiting() writes a
task note (dispatch_loop.py:733-734) and a tick-log line (:735); both are prose a command
cannot check, which is what let "the release is on record" be claimed with no record.

The fixture drives route_waiting with a temp json path and a fake taskstore, so no live state
is touched: no task file, no real ledger, no model, no process, no subprocess.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import dispatch_loop

# A chunk name carrying a task id, which is the form route_waiting resolves: it reads the id
# out of the chunk name with re.search(r"-(t\d+)-", ...) at dispatch_loop.py:687.
CHUNK = "p42-comply-t373-chatui-model-call-outside-contract"
TID = "t373"
PARK = ("PARKED: supervising, parked it -- the brief failed on every model that has tried it "
        "(max switches 1). Edit the brief to release it.")
PRIOR_AT = "2026-09-29T18:18:09Z"          # rca/bb9f66ff7673.md:15, the deferral being audited


class FakeStore:
    PRODUCTION = "production"

    def __init__(self, needs="claude", status="queued"):
        self.needs = {TID: needs}
        self.status = status
        self.notes = []

    def find(self, tid):
        return {"status": self.status, "needs": self.needs.get(tid, "")}, ""

    def set_meta(self, tid, **kw):
        self.needs[tid] = kw.get("needs", self.needs.get(tid))

    def assignee(self, needs):
        return needs or self.PRODUCTION

    def add_note(self, tid, text, who=""):
        self.notes.append((tid, text, who))


def captured(fn, *a, **kw):
    """Run fn with dispatch_loop.record captured into a list. Returns (rows, fn's return)."""
    original = dispatch_loop.record
    rows = []
    dispatch_loop.record = lambda row: rows.append(row)
    try:
        out = fn(*a, **kw)
    finally:
        dispatch_loop.record = original
    return rows, out


def seeded(tmp, name="auto-assigned.json"):
    path = tmp / name
    path.write_text(json.dumps({TID: {"to": "claude", "chunk": CHUNK,
                                      "why": PARK, "at": PRIOR_AT}}, indent=1) + "\n")
    return path


def test_the_ledger_writer_exists():
    # Precondition, checked not assumed: the file defines record() (search 'def record'
    # scripts/dispatch_loop.py) and a module LEDGER it appends to. If either is gone this
    # oracle is scoped against a mechanism that no longer exists and must be re-scoped.
    assert hasattr(dispatch_loop, "record"), "no record() in dispatch_loop.py"
    assert hasattr(dispatch_loop, "LEDGER"), "no LEDGER in dispatch_loop.py"


def test_record_appends_to_the_ledger_and_stamps_the_time():
    # record() writes to the module LEDGER, so a test that patches the global is a real test,
    # not a coincidence. The 'at' stamp is record()'s own work (same shape as supervise.record,
    # supervise.py:85-88) -- a release row with no time is undateable, and the RCA's open
    # question D4 is a date query (at>2026-09-29T18:18:09Z).
    tmp = Path(tempfile.mkdtemp(prefix="t1399-ledger-"))
    original = dispatch_loop.LEDGER
    try:
        dispatch_loop.LEDGER = tmp / "dispatch-loop.jsonl"
        dispatch_loop.record({"event": "probe", "chunk": CHUNK})
        lines = (tmp / "dispatch-loop.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1, f"record() wrote {len(lines)} line(s) to the module LEDGER"
        row = json.loads(lines[0])
        assert row.get("event") == "probe"
        assert row.get("at"), f"record() wrote no 'at': {row!r}"
    finally:
        dispatch_loop.LEDGER = original


def test_unparking_a_brief_writes_a_released_row():
    tmp = Path(tempfile.mkdtemp(prefix="t1399-release-"))
    path, store = seeded(tmp), FakeStore()
    rows, out = captured(dispatch_loop.route_waiting, [], path, store)
    released = [r for r in rows if r.get("event") == "RELEASED"]
    assert len(released) == 1, (
        f"expected exactly one RELEASED row, got {len(released)}: {rows!r}. The release path "
        "(dispatch_loop.py:729-736) writes a task note and a tick-log line and no ledger row, "
        "so no command can check that a park was ever released (D2).")
    row = released[0]
    # Asserted as required fields and values, not shape: a row carrying only the event name
    # records nothing a later session can act on.
    assert row.get("task") == TID, f"no task id in the release row: {row!r}"
    assert row.get("chunk") == CHUNK, f"no chunk in the release row: {row!r}"
    assert row.get("to") == "production", f"release must name where the task went: {row!r}"
    assert CHUNK in str(row.get("why", "")), (
        f"the release row does not name the chunk it released: {row!r}")
    assert PARK in str(row.get("why", "")), (
        f"the release row lost the park reason it is releasing from ({PARK!r}): {row!r} -- "
        "RCA section 5 asks the row to name the prior fix, and the prior fix of a park is the "
        "brief content recorded in the park reason (dispatch_loop.py:37: editing the brief "
        "is the act that releases a park).")
    assert row.get("kind") == "production", f"release row has no kind: {row!r}"
    # The note and the tick line are kept: the row ADDS a machine-checkable record, it does
    # not replace the human-facing one.
    assert out and any("RELEASED" in line and TID in line for line in out), (
        f"the tick log lost its RELEASED line: {out!r}")
    assert store.notes and TID in store.notes[0][0], (
        f"the task lost its release note: {store.notes!r}")
    assert store.needs[TID] == "", f"task was not returned to production: {store.needs[TID]!r}"


def test_a_chunk_still_parked_is_not_released():
    tmp = Path(tempfile.mkdtemp(prefix="t1399-still-"))
    path, store = seeded(tmp, "still.json"), FakeStore()
    rows, _out = captured(dispatch_loop.route_waiting, [(CHUNK, PARK)], path, store)
    assert not [r for r in rows if r.get("event") == "RELEASED"], (
        f"a tick that released nothing wrote a release row: {rows!r} -- a RELEASED row that "
        "lies is the defect this task is about, reproduced in the fix for it.")
    assert store.needs[TID] == "claude", (
        f"the task was taken off claude while its chunk is still parked: {store.needs[TID]!r}")
    assert store.notes == [], f"notes written for a task that is still parked: {store.notes!r}"


def test_a_tick_with_nothing_recorded_writes_nothing():
    tmp = Path(tempfile.mkdtemp(prefix="t1399-empty-"))
    path = tmp / "empty.json"
    path.write_text("{}\n")
    rows, out = captured(dispatch_loop.route_waiting, [], path, FakeStore())
    assert rows == [] and out == [], f"an empty tick produced records: {rows!r} {out!r}"


if __name__ == "__main__":
    tests = sorted((n, f) for n, f in list(globals().items())
                   if n.startswith("test_") and callable(f))
    failed, ran = [], 0
    for name, fn in tests:
        ran += 1
        try:
            fn()
            print(f"  ok   {name}")
        except Exception as e:                                       # noqa: BLE001
            failed.append(name)
            print(f"  FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n  {ran} test(s) run, {len(failed)} failed")
    if ran < 5:
        print(f"  FAIL: expected 5 tests, ran {ran} -- tests must not be removed from this oracle")
        sys.exit(1)
    sys.exit(1 if failed else 0)
