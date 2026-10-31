#!/usr/bin/env python3
"""A skip reason must not describe a situation the tick's own numbers contradict.

2026-10-28 for an hour: 161 ready chunks skipped as "waiting -- every model has a lane", in ticks that
reported running: 0 while every build model had four free slots. The lanes were spent on PLANNED
reviews, so production built nothing and the Hub showed "154 ready to build" -- both true, and the
sentence explaining it false. This suite pins the rule that catches that class.

RCA: workspace/rca/rca-lane-starvation-2026-10-28.md
"""
import sys
import tempfile
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import check_workspace as CW
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'BAD'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    lane = [["p1-t1-x", "waiting -- every model has a lane"]]
    check("skipping for a lane while NOTHING runs is the contradiction",
          CW.lane_contradiction({"at": "t", "running": [], "skipped": lane}) == (1, 0),
          str(CW.lane_contradiction({"at": "t", "running": [], "skipped": lane})))
    check("  the same skip with a job genuinely holding a lane is not a contradiction",
          CW.lane_contradiction({"at": "t", "running": [{"model": "swift"}], "skipped": lane}) is None)
    check("  another skip reason is not this check's business",
          CW.lane_contradiction({"at": "t", "running": [],
                                 "skipped": [["p2-t2-y", "target green -- already on main"]]}) is None)
    check("  a tick that could not be read is None, never a pass", CW.lane_contradiction(None) is None)

    # DRIVE THE METHOD, on a fixture ledger, so the wiring is proved and not just the helper.
    d = Path(tempfile.mkdtemp())
    (d / "workspace").mkdir(parents=True)
    (d / "workspace" / "dispatch-loop.jsonl").write_text(json.dumps(
        {"at": "2026-10-28T05:34:00Z", "event": "tick", "running": [], "skipped": lane}) + "\n")

    class Fake:
        def __init__(self, live):
            self.live, self.results = live, []
        def section(self, *a, **k):
            pass
        def result(self, status, name, detail=""):
            self.results.append((status, name, detail))

    f = Fake(d)
    sys.path.insert(0, str(ROOT / "scripts"))
    import jsonl_cache
    jsonl_cache.latest_tick = lambda path: {"at": "2026-10-28T05:34:00Z", "running": [], "skipped": lane}
    CW.Checker.check_dispatch_lane_contradiction(f)
    check("the method FAILS on the tick that started this RCA",
          bool(f.results) and f.results[0][0] == "FAIL", str(f.results[:1]))
    check("  and it points at the RCA", "rca-lane-starvation" in str(f.results[0][2]), str(f.results[:1]))
    # THE LIVE TICK WRITES running AS AN INT (2026-10-28): len() on it crashed the whole check.
    check("a tick whose running is a COUNT is read, not crashed on (0 -> contradiction)",
          CW.lane_contradiction({"at": "t", "running": 0, "skipped": lane}) == (1, 0))
    check("  and a positive count is not a contradiction",
          CW.lane_contradiction({"at": "t", "running": 2, "skipped": lane}) is None)
    src = (ROOT / "scripts" / "check_workspace.py").read_text(encoding="utf-8")
    check("  the check is DISPATCHED from run(), not merely defined",
          "(self.check_dispatch_lane_contradiction, ())" in src)
    print("test_lane_contradiction:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
