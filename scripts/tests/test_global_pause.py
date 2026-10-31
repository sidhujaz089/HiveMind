#!/usr/bin/env python3
"""Press pause once and NO model works on any task (Jas, 2026-10-30).

Jas: *"Fix the gaps in production so when I press pause it stops every model from working on tasks."*

Measured before this: of the seven things that start work, exactly ONE consulted a pause flag --
scripts/scoping_queue.sh -- and even that only half way, because its device check sits inside `if [ "$SCOPER" !=
manager ]` (scoping_queue.sh:176). The bridge, bridge_trigger, scope_queue, night_queue, autonomy_loop and supervise
consulted neither flag. So Jas pressed pause on both devices and the manager loop went straight on scoping (its own
progress.log: "03:43:03Z START t9580 ... role manager").

There are two flags and they mean different things, which is why one loop obeyed and another did not:

  * workspace/run-logs/scheduler-paused.enabled      -- the production screen (is_paused())
  * workspace/run-logs/device-paused-<dev>.enabled   -- one device (paused_devices())

must_hold() is the single question a producer asks, and it answers for BOTH: the screen, or every device, or the
device this work would use. A producer that asks it cannot be selective by accident.

RED ON MAIN: scheduler_commands has no must_hold.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import scheduler_commands as sc         # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


check("nothing paused means GO", sc.must_hold(paused=False, devices={}) == (False, ""), str(sc.must_hold(paused=False, devices={})))

ok, why = sc.must_hold(paused=True, devices={})
check("the production screen paused holds everything", ok is True and "screen" in why.lower(), why)

both = {"gx10": {"who": "jas"}, "thinkpad": {"who": "jas"}}
ok, why = sc.must_hold(paused=False, devices=both, known=("gx10", "thinkpad"))
check("BOTH devices paused holds everything, even a producer that names no device",
      ok is True and "all devices" in why.lower(), why)
check("  and it says who paused them", "jas" in why, why)

one = {"thinkpad": {"who": "jas"}}
ok, why = sc.must_hold(paused=False, devices=one, known=("gx10", "thinkpad"), device="thinkpad")
check("one device paused holds the work on THAT device", ok is True and "thinkpad" in why.lower(), why)
check("  and leaves work on another device alone",
      sc.must_hold(paused=False, devices=one, known=("gx10", "thinkpad"), device="gx10") == (False, ""), "")

ok, why = sc.must_hold(paused=False, devices={"gx10": {}}, known=("gx10", "thinkpad"), role="manager")
check("a role with a known device is judged on that device", ok is True and "gx10" in why.lower(), why)

# the asymmetry that caused this: a producer must not be able to ask for "the screen only"
ok, why = sc.must_hold(paused=False, devices=both, known=("gx10", "thinkpad"), device=None)
check("naming NO device does not escape a full pause", ok is True, why)

# --- EVERY PLACE A MODEL ACTUALLY STARTS MUST ASK ------------------------------------------------------
# The rule this enforces is a SPAWN, not a mention: these files build a command list containing ask_q16.py, which
# means they are where a model begins working. A file that merely names ask_q16 in prose (lane_status's label map,
# hivestatus and memory_watch's process scans, scoping_lanes' bookkeeping) is an observer and is not one of these.
SPAWNERS = {
    "scripts/assigned_worker.py": "the assigned-items worker",
    "scripts/scope_gate.py": "the scoping gate's rewrite round",
    "scripts/session_review.py": "session review",
    "scripts/session_review_trial.py": "the session-review trial",
    "scripts/task_worker.py": "the task worker",
}
for rel, what in SPAWNERS.items():
    src = (HERE / rel).read_text(encoding="utf-8")
    spawns = 'ask_q16.py")' in src or "ask_q16.py')" in src or 'ask_q16.py"' in src
    check(f"{what} asks must_hold before it starts a model", spawns and "must_hold" in src,
          "it spawns the model runner and never asks the pause")
# and the shell loop that drives scoping asks it too
sh = (HERE / "scripts" / "scoping_queue.sh").read_text(encoding="utf-8")
check("the scoping loop asks must_hold for EVERY scoper",
      "must_hold" in sh and 'must_hold(role=' in sh, "the loop can still be selective")

print("test_global_pause:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
