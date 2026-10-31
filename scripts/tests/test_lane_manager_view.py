#!/usr/bin/env python3
"""lane_status's MANAGER QUEUE for the open-ended queue: running (any runner), done today, next.

The first version listed a fixed job directory and went blank when the manager switched to taking
the top of QUEUE.md. Fixtures only.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import lane_status as LS  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    # NOON UTC TODAY, NOT THE WALL CLOCK (2026-10-17): at 00:00-01:00 UTC the fake job an hour back
    # fell on yesterday's date, the test went red, and red-on-main held every build for that hour.
    now = time.time() // 86400 * 86400 + 43200
    day = int(now % 86400)
    hh = lambda s: time.strftime("%H:%M:%S", time.gmtime(s % 86400))  # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        old, new = t / "old.log", t / "new.log"
        old.write_text(f"{hh(day-3600)}Z START p20-rethink (task x)\n{hh(day-2400)}Z END p20-rethink rc=0 bytes=14360\n")
        new.write_text("")
        ptr = t / "q.json"
        ptr.write_text(json.dumps({"in": str(t), "out": str(t), "progress": str(new), "also": [str(old)]}))
        lanes = [{"kind": "scoping", "brief": "claude-driven manager: t388-finalise", "elapsed_s": 180},
                 {"kind": "production", "brief": "brief-x.txt", "elapsed_s": 50}]
        v = LS.manager_view(lanes, pointer=ptr, now=now, next_n=3)
    report("a running manager job shows, from any runner", v["running"],
           [{"job": "t388-finalise", "elapsed_s": 180}])
    report("done today comes from every progress log named", v["done"],
           [{"job": "p20-rethink", "bytes": 14360}])
    report("the median is measured from today's jobs", (v["median_s"], v["measured"]), (1200, 1))
    report("next comes from QUEUE.md (a list)", isinstance(v["next"], list), True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
