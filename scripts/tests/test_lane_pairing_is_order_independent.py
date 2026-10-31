#!/usr/bin/env python3
"""t4386: a lane is open because its start has no finish, not because of the ORDER they were written.

p487-t1090-flow-coverage sat "never closed ... not believed" from 2026-10-14 to 2026-10-25. Reading
the rows, both sides were right about the pid:

    2026-10-14T00:47:58Z started  pid 2317751
    2026-10-14T00:48:17Z finished pid 2320357   <- a finish logged 2s BEFORE that pid's start
    2026-10-14T00:48:19Z started  pid 2320357
    2026-10-14T00:53:53Z finished pid 2317751

The launch writer records `p.pid` from the child it just started (dispatch_loop.py:2654) and the
finish writers copy the pid out of the started row (dispatch_loop.py:98 and :567), so neither invents
a pid. What can happen is that the CHILD logs its own terminal row before the PARENT gets round to
logging the start. A single pass that pops on every finish then closes the wrong lane: it pops
2320357's finish against a key that does not exist yet, and 2317751's finish closes 2317751, leaving
2320357 open for ever even though it ended.

The fix is that pairing must be order-independent: count starts and finishes per (chunk, pid) and
call a lane open only when it has more starts than finishes. This cannot close the wrong lane,
because it never pairs a finish with anything -- it compares totals for the same key.

Hermetic: rows are literals, `now` is passed in, nothing is read or written.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402

NOW = "2026-10-25T21:00:00Z"
FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def row(event, pid, at, chunk="p487-t1090-flow-coverage", kind="production"):
    return {"event": event, "chunk": chunk, "pid": pid, "at": at, "kind": kind}


def lanes(rows):
    return cw.liveness_running_lanes(rows, now=NOW)


def main():
    # 1. THE p487 SHAPE: the child's finish is logged before the parent's start.
    running, stale = lanes([
        row("started", 2317751, "2026-10-25T20:47:58Z"),
        row("finished", 2320357, "2026-10-25T20:48:17Z"),
        row("started", 2320357, "2026-10-25T20:48:19Z"),
        row("finished", 2317751, "2026-10-25T20:53:53Z")])
    check("a finish logged before its own start still closes the lane",
          (len(running), len(stale)) == (0, 0), (len(running), len(stale)))

    # 2. THE NORMAL SHAPE, which must keep working.
    running, stale = lanes([row("started", 11, "2026-10-25T20:50:00Z"),
                            row("finished", 11, "2026-10-25T20:51:00Z")])
    check("start then finish is closed", (len(running), len(stale)) == (0, 0), (len(running), len(stale)))

    # 3. A LANE THAT REALLY IS OPEN, recent -> running; old -> stale.
    running, stale = lanes([row("started", 12, "2026-10-25T20:55:00Z")])
    check("a start with no finish at all is still a running lane",
          (len(running), len(stale)) == (1, 0), (len(running), len(stale)))
    running, stale = lanes([row("started", 13, "2026-10-20T00:00:00Z")])
    check("and an old one is stale", (len(running), len(stale)) == (0, 1), (len(running), len(stale)))

    # 4. TWO RUNS OF ONE CHUNK, each with its own finish, in any order -> both closed.
    running, stale = lanes([row("started", 21, "2026-10-25T20:40:00Z"),
                            row("started", 22, "2026-10-25T20:41:00Z"),
                            row("finished", 22, "2026-10-25T20:42:00Z"),
                            row("finished", 21, "2026-10-25T20:43:00Z")])
    check("two overlapping runs of one chunk both close",
          (len(running), len(stale)) == (0, 0), (len(running), len(stale)))

    # 5. A FINISH WITH NO START ANYWHERE is not an open lane -- there is nothing to close, and
    #    inventing one would report a lane that was never opened.
    running, stale = lanes([row("finished", 31, "2026-10-25T20:45:00Z")])
    check("a finish with no start is not a lane", (len(running), len(stale)) == (0, 0),
          (len(running), len(stale)))

    # 6. A SECOND START WITHIN ONE KEY (a retry of the same pid shape) is still open once.
    running, stale = lanes([row("started", 41, "2026-10-25T20:44:00Z"),
                            row("finished", 41, "2026-10-25T20:45:00Z"),
                            row("started", 41, "2026-10-25T20:58:00Z")])
    check("one closed run plus one open run under the same key is open once",
          (len(running), len(stale)) == (1, 0), (len(running), len(stale)))

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
