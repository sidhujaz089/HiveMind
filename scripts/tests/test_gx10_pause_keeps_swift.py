#!/usr/bin/env python3
"""A person's GX10 pause stops production but keeps Swift serving (Jas, 2026-10-22: "I expect to pause and
resume production a lot"). The Hub's GX10 Pause called gx10_off, which stopped all of production.target --
Swift included -- so the GX10 DeepSeek Harness, which runs on Swift, failed ("OpenAI-compatible API
request to http://172.18.0.2:8000/v1 failed") every time production was paused, and every Resume paid a
multi-minute 93 GB model load.

Contract (systemctl is faked; nothing real is stopped):
  - gx10_off("jas", ...) stops every unit of production.target EXCEPT swift.service, and never stops the
    target itself (stopping a target stops its PartOf units, Swift among them);
  - gx10_off("memory-guard", ...) still stops production.target: freeing Swift's memory is the point;
  - both still record the GX10's device pause.

RED ON MAIN for the stated reason only: gx10_off stops production.target for every caller.
"""
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
    deps = "production.target\n  scoping-queue@auditor.service\n  scoping-queue@manager.service\n  swift.service\n"

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        r = _R()
        if "list-dependencies" in cmd:
            r.stdout = deps
        return r

    import subprocess
    real = subprocess.run
    subprocess.run = fake_run
    try:
        ok, _ = SC.gx10_off("jas", "paused from the Hub")
        stops = [c for c in calls if "stop" in c]
        flat = [x for c in stops for x in c]
        check("a person's pause succeeds", ok, True)
        check("...stops the Manager queue", "scoping-queue@manager.service" in flat, True)
        check("...stops the Auditor's scoping", "scoping-queue@auditor.service" in flat, True)
        check("...does NOT stop Swift", "swift.service" in flat, False)
        check("...does NOT stop the target (that would stop Swift too)", "production.target" in flat, False)
        check("...and pauses the GX10", "gx10" in SC.paused_devices(), True)

        calls.clear()
        SC.resume_device("gx10", sweep=False)
        ok, _ = SC.gx10_off("memory-guard", "pressure 80%")
        flat = [x for c in calls if "stop" in c for x in c]
        check("the memory guard's pause still stops production.target", "production.target" in flat, True)
    finally:
        subprocess.run = real

print(f"{'FAILED' if fails else 'ok'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
