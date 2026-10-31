#!/usr/bin/env python3
"""The scheduler button is the master breaker (Jas, 2026-10-06): pause stops production.target,
resume starts it, and at boot the line NEVER starts: boot pauses and stops it (Jas, 2026-10-09,
after Swift took the box down: "after a reboot the master key is off and no models boot up").
Hermetic: the pause flag lives in a temp dir, systemctl is faked, and the device-pause LEDGER
is redirected to the same temp dir (t-3b, 2026-10-23: this suite wrote 192 fake rows into the
live workspace/run-logs/device-pause.jsonl -- the PAUSE_FLAG redirect moved the flags, not the
ledger, which the recorder reads from its own module constant at call time)."""
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
    returncode, stderr = 0, ""


calls = []
sc._systemctl = lambda *a: (calls.append(a), R())[1]
sc.PAUSE_FLAG = Path(tempfile.mkdtemp()) / "scheduler-paused.enabled"
sc.DEVICE_PAUSE_LEDGER = sc.PAUSE_FLAG.parent / "device-pause.jsonl"

# THE LIVE LEDGER MUST BE REFUSED, here, in this process (t-3b): the guard exists so that a
# suite which forgets the redirect above fails loudly at the primitive.
try:
    sc.refuse_live_device_pause_ledger(sc.REPO / "workspace" / "run-logs" / "device-pause.jsonl")
    check("the live-ledger guard refuses the live path in a test process", False)
except RuntimeError:
    check("the live-ledger guard refuses the live path in a test process", True)

sc.breaker_off("jas", "test")
check("pause sets the flag", sc.is_paused())
check("pause stops the production line", calls[-1] == ("stop",))

calls.clear()
sc.boot()
check("boot while paused starts nothing (it stops)", calls == [("stop",)] and sc.is_paused())

sc.breaker_on()
check("resume clears the flag", not sc.is_paused())
check("resume starts the production line", calls[-1] == ("start",))

calls.clear()
sc.boot()
check("boot while running does NOT start the line: it pauses and stops it",
      calls == [("stop",)] and sc.is_paused())

calls.clear()
sc.pause("session_end", "the test sweep")
check("session_end's pause() does not touch the line (Swift stays loaded)", calls == [])
sc.resume()

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
