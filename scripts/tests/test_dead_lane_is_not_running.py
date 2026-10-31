#!/usr/bin/env python3
"""A lane whose PROCESS IS GONE is not running, whatever its model is.

FOUND LIVE, 2026-10-29: two lanes (pids 1596355, 1596356) started at 22:47Z, never wrote a finished row, and
/proc/<pid> did not exist -- while the tick reported running: 2, started 0, and 169 chunks "waiting for a free
lane" with 18.7 GB of memory free. Two phantom lanes were the shortage, and the reason said memory or lanes.

WHY THE DETECTOR MISSED THEM: wedged() asked only "is the model NOT resident and is the log silent". Swift's
vLLM is resident all day on this box, so a dead job counted as running for ever. The missing question is the
obvious one: is the pid alive?

The old heuristic is kept -- a model that unloaded under a live pid is still a wedged lane -- so this adds a
case rather than replacing one.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import dispatch_loop as D
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    me = os.getpid()
    dead = 999999                                   # above the pid ceiling: nothing can own it
    check("pid_alive() sees this process", D.pid_alive(me), "")
    check("  and not a pid that does not exist", not D.pid_alive(dead), "")
    check("  an unusable value is 'cannot tell', not 'dead'", D.pid_alive(None) and D.pid_alive("x"), "")

    # THE CASE THAT COST A DAY: dead pid + model resident.
    lanes = [{"pid": dead, "model": "swift", "log_age_s": 10}]
    check("a DEAD pid with its model still resident is a wedged lane",
          D.wedged(lanes, {"swift"}) == lanes, D.wedged(lanes, {"swift"}))
    check("  so two dead lanes are two free lanes", len(D.wedged(
        [{"pid": dead, "model": "swift"}, {"pid": dead + 1, "model": "swift"}], {"swift"})) == 2, "")

    # THE OLD CASE MUST STILL HOLD: live pid, model gone, log silent.
    live_unloaded = [{"pid": me, "model": "gemma", "log_age_s": D.WEDGE_S + 60}]
    check("a LIVE pid whose model unloaded and log went silent is still wedged",
          D.wedged(live_unloaded, {"swift"}) == live_unloaded, D.wedged(live_unloaded, {"swift"}))
    live_loaded = [{"pid": me, "model": "swift", "log_age_s": D.WEDGE_S + 60}]
    check("  but a live pid with its model loaded is NOT wedged",
          D.wedged(live_loaded, {"swift"}) == [], D.wedged(live_loaded, {"swift"}))

    # THE SELF-CONFIRMING LOOP: a dead lane must not be able to make its brief "already running" for ever by
    # entering `running`, which is what the next tick reads as evidence that the job exists.
    src_loop = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    i = src_loop.index("lanes = [r for r in lanes if r not in stuck]")
    window = src_loop[i:i + 900]
    check("the tick drops a lane whose process is gone before it becomes 'running'",
          'not pid_alive(r["pid"])' in window, "a dead lane can still enter `running` and re-confirm itself")
    check("  and it SAYS so rather than vanishing quietly", "LOST      pid" in window, "")

    # AND THE STATUS COMMAND MUST SURVIVE AN OLD ROW.
    src = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    i = src.index("def status():")
    window = src[i:i + 6000]
    check("`--status` reads every field defensively (an old row must not crash it)",
          'r.get("accepted")' in window and "r.get('seconds', '?')" in window,
          "status still indexes a key an older writer may not have set")
    print("test_dead_lane_is_not_running:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
