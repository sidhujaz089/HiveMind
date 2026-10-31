#!/usr/bin/env python3
"""Two writers must not hand out the same change number (2026-10-30).

The changelog's `n` came from `max(n) + 1` read out of ONE checkout. On 2026-10-30 a session end in the live tree and
this session in a worktree both wrote rows 104 and 105 for different changes, so the two could never be merged
cleanly -- the merge would have carried two different histories under one number. That is what "multiple session
ends at the same time" actually costs, and it is not the lock people think of: it is an allocator with no owner.

Two halves, because a lock alone cannot fix two checkouts:
  * _write() takes a LOCK and allocates in the same critical section, so two writers in one tree serialise;
  * renumber() resolves duplicates that already exist, deterministically, by timestamp -- which is what makes two
    checkouts mergeable at all.

RED ON MAIN: flow_change has no renumber at all --
  BAD  renumber resolves a duplicate by timestamp
"""
import json
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import flow_change as fc          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


def seed(tmp, rows):
    log = Path(tmp) / "FLOW-CHANGELOG.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return log


with tempfile.TemporaryDirectory() as td:
    log = seed(td, [
        {"n": 104, "at": "2026-10-30T03:41:40Z", "who": "a", "what": "helper scripts/migrate_needs_key.py"},
        {"n": 104, "at": "2026-10-30T05:10:00Z", "who": "b", "what": "helper scripts/test_verdict_cache.py"},
        {"n": 105, "at": "2026-10-30T05:11:00Z", "who": "b", "what": "helper scripts/tests/test_verdict_cache.py"},
    ])
    old = (fc.LOG, fc.FLOW) if hasattr(fc, "FLOW") else (fc.LOG, None)
    fc.LOG = log
    try:
        mapping = fc.renumber()
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    finally:
        fc.LOG = old[0]
    numbers = [r["n"] for r in rows]
    check("renumber leaves no duplicate numbers", len(numbers) == len(set(numbers)), str(numbers))
    check("  and keeps the earliest claim of a number", rows[0]["n"] == 104, str(rows[0]))
    check("  and renumbers the later duplicate after the highest", sorted(numbers) == [104, 105, 106], str(numbers))
    check("  and reports what it changed", mapping == {104: 106}, str(mapping))
    check("  and order by timestamp is what decides", [r["at"] for r in rows] == sorted(r["at"] for r in rows),
          [r["at"] for r in rows])

with tempfile.TemporaryDirectory() as td:
    log = seed(td, [])
    fc.LOG = log
    got = []
    def writer(k):
        for _ in range(20):
            got.append(fc._append_number())
    try:
        ts = [threading.Thread(target=writer, args=(k,)) for k in range(4)]
        for t in ts: t.start()
        for t in ts: t.join()
    finally:
        rows = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("80 concurrent allocations (4 threads x 20) hand out 80 distinct numbers",
          len(rows) == 80 and len({r["n"] for r in rows}) == 80,
          f"{len(rows)} rows, {len({r['n'] for r in rows})} distinct")

print("test_flow_change_numbers:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
