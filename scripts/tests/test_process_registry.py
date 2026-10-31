"""t643: a change to the box's cron lines or enabled user units is recorded, with whether each
added item is on the Flow chart. No live crontab/systemctl is read: snapshots are passed in."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import process_registry as P

ok = fail = 0
def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}"); ok += bool(cond); fail += not cond

# NEVER THE LIVE SNAPSHOT (2026-10-02): record(prev=None) reads P.SNAP, and coder's sandbox copies the
# working tree -- live workspace/run-logs/process-registry.json included -- so "the first snapshot" was
# not a first one there, this suite went red only in the sandbox, and every build refused to start.
import tempfile as _tf
P.SNAP = Path(_tf.mkdtemp()) / "process-registry.json"
base = {"cron": ["*/10 * * * * python3 scripts/dispatch_loop.py"], "units": {"scoping-queue@manager.service": True}}
flow = '"dispatch_loop" "manager-queue" "nas-backup"'
check("the first snapshot is a baseline, not a change", P.record(base, prev=None, flow_text=flow, write=False) is None)
check("no change, no row", P.record(base, prev=base, flow_text=flow, write=False) is None)
new = {"cron": base["cron"] + ["*/15 * * * * bash scripts/timer-gemma.sh"],
       "units": {"scoping-queue@manager.service": True, "nas-backup.timer": True}}
row = P.record(new, prev=base, flow_text=flow, write=False)
check("an added cron line and unit are recorded", row and row["added"] == ["cron: */15 * * * * bash scripts/timer-gemma.sh", "nas-backup.timer"])
check("  and the off-chart one is named", row and row["off_chart"] == ["cron: */15 * * * * bash scripts/timer-gemma.sh"])
row = P.record(base, prev=new, flow_text=flow, write=False)
check("a removal is recorded too (the timers that vanished with no trail)",
      row and set(row["removed"]) == {"cron: */15 * * * * bash scripts/timer-gemma.sh", "nas-backup.timer"})
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
