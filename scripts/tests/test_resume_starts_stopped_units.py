#!/usr/bin/env python3
"""Resume must start the production units a person's pause stopped, even though production.target
stays active (t1557). The Hub's GX10 Pause calls gx10_off, which for a person stops the individual
units (manager-queue.service, auditor-scoping.service) but NOT production.target -- so the target
remains 'active'. Resume calls gx10_on, which today only runs `start production.target` -- a no-op
while the target is already active -- and brings neither unit back. They stayed down 52 minutes.

Contract (systemctl is faked; nothing real is started or stopped):
  - after a person's pause, gx10_on starts manager-queue.service and auditor-scoping.service;
  - gx10_on does NOT force a Swift reload (swift.service is not started);
  - gx10_on still starts production.target (the memory-guard / boot case stops the target itself).

RED ON MAIN for the stated reason only: gx10_on starts only production.target, never the units.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import scheduler_commands as SC  # noqa: E402

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


class _R:
    returncode, stdout, stderr = 0, "", ""


with tempfile.TemporaryDirectory() as td:
    SC.PAUSE_FLAG = Path(td) / "scheduler-paused.enabled"
    SC.DEVICE_PAUSE_LEDGER = Path(td) / "device-pause.jsonl"
    calls = []
    deps = ("production.target\n"
            "  auditor-scoping.service\n"
            "  manager-queue.service\n"
            "  swift.service\n")

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        r = _R()
        if "list-dependencies" in cmd:
            r.stdout = deps
        return r

    real = subprocess.run
    subprocess.run = fake_run
    try:
        # A person's pause: stops the units, leaves production.target active.
        ok, _ = SC.gx10_off("jas", "paused from the Hub")
        check("a person's pause succeeds", ok, True)
        check("...stops the units, not the target",
              any("stop" in c and "production.target" not in c for c in calls), True)

        calls.clear()
        # Resume: must bring the stopped units back even though the target never stopped.
        ok, _ = SC.gx10_on()
        starts = [c for c in calls if "start" in c]
        flat = [x for c in starts for x in c]
        check("Resume succeeds", ok, True)
        check("Resume starts the Manager queue", "manager-queue.service" in flat, True)
        check("Resume starts the Auditor's scoping", "auditor-scoping.service" in flat, True)
        check("Resume does NOT force a Swift reload", "swift.service" in flat, False)
        check("Resume still starts production.target (guard/breaker case)",
              "production.target" in flat, True)
    finally:
        subprocess.run = real

print(f"{'FAILED' if fails else 'ok'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
