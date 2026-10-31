#!/usr/bin/env python3
"""M1 (HiveMind L3, 2026-10-26): Swift can be served WITHOUT resuming production. swift.service is
PartOf=production.target, which binds stop only -- starting the unit alone is legal -- but the only
code path that started Swift was a full Resume (production.target). swift_on() starts swift.service
by itself and leaves every pause flag exactly as it was. Hermetic: subprocess.run is faked."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scheduler_commands as sc

ok = fail = 0


def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}")
    ok += bool(cond); fail += not cond


class R:
    returncode, stderr, stdout = 0, "", ""


runs = []
sc._subprocess.run = lambda argv, **kw: (runs.append(list(argv)), R())[1]
sc._systemctl = lambda *a: (runs.append(["TARGET", *a]), R())[1]
sc.PAUSE_FLAG = Path(tempfile.mkdtemp()) / "scheduler-paused.enabled"
sc.DEVICE_PAUSE_LEDGER = sc.PAUSE_FLAG.parent / "device-pause.jsonl"

sc.boot()
runs.clear()
okk, err = sc.swift_on()
check("swift_on reports success", okk and err == "")
check("swift_on starts swift.service alone",
      runs == [["systemctl", "--user", "--no-block", "start", "swift.service"]])
check("the line stays paused", sc.is_paused())
check("the GX10 stays paused", "gx10" in sc.paused_devices())
check("production.target is never touched", not any(r[0] == "TARGET" for r in runs))

print(f"\n{ok} ok, {fail} fail")
sys.exit(1 if fail else 0)
