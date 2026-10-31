#!/usr/bin/env python3
"""t1557: Resume brings back a production unit that is down while production.target is active.

2026-10-23: manager-queue and auditor-scoping were stopped (by a test, since fixed) and
production.target stayed 'active' with both dead. `systemctl start production.target` on an active
target starts nothing, so Resume left them down for 52 minutes. Resume must start each unit the
target pulls in, by name. Hermetic: subprocess.run is faked, flags and ledger in a temp dir."""
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


UNITS = ["swift.service", "scoping-queue@manager.service", "scoping-queue@auditor.service"]


class R:
    def __init__(self, out=""):
        self.returncode, self.stderr, self.stdout = 0, "", out


runs = []


def fake_run(argv, **kw):
    runs.append(list(argv))
    if "list-dependencies" in argv:
        return R("production.target\n" + "".join(f"  {u}\n" for u in UNITS))
    return R()


sc._subprocess.run = fake_run
sc._systemctl = lambda *a: (runs.append(["TARGET", *a]), R())[1]
sc.PAUSE_FLAG = Path(tempfile.mkdtemp()) / "scheduler-paused.enabled"
sc.DEVICE_PAUSE_LEDGER = sc.PAUSE_FLAG.parent / "device-pause.jsonl"
import subprocess
subprocess.run = fake_run          # _production_units imports subprocess itself

for name, fn in (("Resume GX10", sc.gx10_on), ("Resume (all)", sc.breaker_on)):
    runs.clear()
    done, err = fn()
    named = {u for r in runs if r[:2] == ["TARGET", "start"] for u in r[2:]}
    check(f"{name} reports success", done)
    check(f"{name} starts production.target last", runs[-1] == ["TARGET", "start"])
    check(f"{name} starts every production unit by name", set(UNITS) <= named)

print(f"\n{ok} ok, {fail} fail")
sys.exit(1 if fail else 0)
