#!/usr/bin/env python3
"""GATE: the Status-tab control primitives Jas asked for (t316-ish) -- pause/resume the
scheduler, stop one running job by pid, and move an enrolled chunk to the front of the
queue -- each as a small, testable function in a new module, scripts/scheduler_commands.py.

Jas, 2026-09-15: proposed Status-tab buttons -- pause scheduler, stop job, move to front,
unpark, run tick, wedge toggle, ollama restart via sudoers. This oracle covers the first
three (pause/resume, stop_job, promote); unpark, run-tick and wedge-toggle either reuse an
existing mechanism directly (wedge-stop.enabled already exists; "run tick" is just invoking
`python3 scripts/dispatch_loop.py`) or are scoped separately (unpark touches
scripts/supervise.py's PARK ledger, a different file). The sudoers button is not delegated
work; Jas asked for it to be built but it is a system-trust decision handled directly, not
through this pipeline.

Each function must be a REFUSAL, not a crash or a silent no-op, when asked to do something
that does not check out -- stopping a pid dispatch_loop is not tracking, or promoting a
chunk name that does not exist in the loop table.
"""
import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


try:
    import scheduler_commands as sc
except ImportError as e:
    print(f"scripts/scheduler_commands.py does not exist yet: {e}")
    print(f"\n0 passed, 1 failed\n")
    sys.exit(1)

print("\nPause / resume -- a flag file, exactly like workspace/run-logs/wedge-stop.enabled:")
orig_flag = sc.PAUSE_FLAG
sc.PAUSE_FLAG = Path(tempfile.mkdtemp()) / "scheduler-paused.enabled"
try:
    check("starts unpaused", sc.is_paused(), False)
    sc.pause(who="jas", why="testing")
    check("is_paused() true after pause()", sc.is_paused(), True)
    state = json.loads(sc.PAUSE_FLAG.read_text())
    check("the flag file names who paused it", state.get("who"), "jas")
    check("and why", state.get("why"), "testing")
    sc.resume()
    check("is_paused() false after resume()", sc.is_paused(), False)
    check("resume() on an already-unpaused scheduler does not raise",
          sc.resume() or "no exception", "no exception")
finally:
    sc.PAUSE_FLAG = orig_flag

print("\nstop_job -- refuses a pid dispatch_loop is not tracking, never sends a bare kill:")
import dispatch_loop as DL  # noqa: E402
orig_ledger_running = DL.ledger_running
DL.ledger_running = lambda history=None: [{"pid": 999999, "kind": "preflight", "model": "m"}]
import lane_status  # noqa: E402
orig_snapshot = lane_status.snapshot
lane_status.snapshot = lambda: {"lanes": [], "resident": []}
try:
    ok, why = sc.stop_job(424242)
    check("refuses an untracked pid", ok, False)
    check("says why", "not" in why.lower() and "424242" in why, True)

    real = os.kill
    killed = []

    def fake_kill(pid, sig):
        killed.append((pid, sig))
    os.kill = fake_kill
    try:
        ok, why = sc.stop_job(999999)
    finally:
        os.kill = real
    check("accepts a pid dispatch_loop IS tracking", ok, True)
    check("sends exactly one signal", len(killed), 1)
    if killed:
        check("it is SIGTERM", killed[0][1], signal.SIGTERM)
        check("to the right pid", killed[0][0], 999999)
finally:
    DL.ledger_running = orig_ledger_running
    lane_status.snapshot = orig_snapshot

print("\npromote -- moves a named chunk's row to the front of the loop table, refuses an "
      "unknown chunk name, and never touches any row's CONTENT, only its position:")
tmp_loop = Path(tempfile.mkdtemp()) / "autonomy_loop.sh"
tmp_loop.write_text(
    "#!/bin/bash\n# header, not a chunk row\n"
    "p10-alpha|brief-a.txt|a.py|scripts/tests/test_a.py\n"
    "p11-beta|brief-b.txt|b.py|scripts/tests/test_b.py\n"
    "p12-gamma|brief-c.txt|c.py|scripts/tests/test_c.py\n",
    encoding="utf-8")
ok, why = sc.promote("p12-gamma", loop_path=tmp_loop)
check("promote() reports success", ok, True)
lines = [l for l in tmp_loop.read_text(encoding="utf-8").splitlines() if l.startswith("p")]
check("the promoted chunk is now first", lines[0].split("|")[0], "p12-gamma")
check("nothing else was rewritten -- same three rows, just reordered",
      sorted(l.split("|")[0] for l in lines), ["p10-alpha", "p11-beta", "p12-gamma"])
ok2, why2 = sc.promote("p99-does-not-exist", loop_path=tmp_loop)
check("promote() refuses an unknown chunk name", ok2, False)
check("says which name", "p99-does-not-exist" in why2, True)

# --- boot: the line is ALWAYS off after a reboot (Jas, 2026-10-09) -------------------------
with tempfile.TemporaryDirectory() as td:
    flag = Path(td) / "scheduler-paused.enabled"
    calls = []
    real_flag, real_sys, real_ledger = sc.PAUSE_FLAG, sc._systemctl, sc.DEVICE_PAUSE_LEDGER
    sc.PAUSE_FLAG = flag
    # t-3b (2026-10-23): boot() records one device-pause row per device into
    # DEVICE_PAUSE_LEDGER -- a module constant, read at call time, NOT derived from
    # PAUSE_FLAG.parent. This suite redirected the flag 70 times and wrote 76 rows into
    # the LIVE ledger (workspace/run-logs/device-pause.jsonl). Redirect the ledger too.
    sc.DEVICE_PAUSE_LEDGER = Path(td) / "device-pause.jsonl"
    sc._systemctl = lambda *a: calls.append(a) or type("R", (), {"returncode": 0, "stderr": ""})()
    try:
        check("not paused before the reboot", flag.exists(), False)
        sc.boot()
        check("boot turns the master key off (paused)", flag.exists(), True)
        check("boot never starts production, it stops it", calls, [("stop",)])
        check("the pause says it was the reboot", json.loads(flag.read_text())["who"], "boot")
        # t-3b: the boot's records landed in the temp ledger, and the live one is refused.
        check("boot recorded to the temp ledger, not the live one",
              all("device" in json.loads(l) for l in sc.DEVICE_PAUSE_LEDGER.read_text().splitlines()),
              True)
        try:
            sc.refuse_live_device_pause_ledger(
                sc.REPO / "workspace" / "run-logs" / "device-pause.jsonl")
            check("the live-ledger guard refuses the live path in a test process", False, True)
        except RuntimeError:
            check("the live-ledger guard refuses the live path in a test process", True, True)
    finally:
        sc.PAUSE_FLAG, sc._systemctl, sc.DEVICE_PAUSE_LEDGER = real_flag, real_sys, real_ledger

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
