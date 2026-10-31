#!/usr/bin/env python3
"""A pause is never overwritten by someone else's pause (Jas, 2026-10-21: "I paused the thinkpad. Why did
it start on its own?").

What happened, from workspace/run-logs/device-pause.jsonl: Jas paused the ThinkPad from the Hub at
02:25:42Z (flag 27701-...-1, who=jas). At 05:13:09Z thinkpad_guard saw 0.84 GB free and called
pause_device, which REWROTE the one flag file -- the pause now said who=thinkpad-guard. At 05:23:10Z
the guard's _resume_own_pause found "its own" pause and cleared it, and production restarted on a
device Jas had stopped. His flag was never cleared by anyone; it was overwritten.

Contract:
  - pause_device on a device already paused by a DIFFERENT holder leaves that holder's pause in place
    and records the refused pause in the ledger (action "already-paused", naming the holder);
  - so the guard's _resume_own_pause never clears it (it is not the guard's);
  - the same holder pausing again may refresh its own pause;
  - Resume (resume_device) still clears whoever holds it -- a person's Resume is the master key.

RED ON MAIN for the stated reason only: pause_device overwrites the flag unconditionally.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import scheduler_commands as SC  # noqa: E402
import thinkpad_guard as TG  # noqa: E402

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


with tempfile.TemporaryDirectory() as td:
    SC.PAUSE_FLAG = Path(td) / "scheduler-paused.enabled"
    SC.DEVICE_PAUSE_LEDGER = Path(td) / "device-pause.jsonl"
    TG.LEDGER = Path(td) / "thinkpad-guard.jsonl"

    SC.pause_device("thinkpad", "jas", "paused from the Hub")
    SC.pause_device("thinkpad", "thinkpad-guard", "ThinkPad RAM 0.84 GB free, under the 1.5 GB reserve")
    check("Jas's pause survives the guard's", SC.paused_devices().get("thinkpad", {}).get("who"), "jas")
    rows = [json.loads(l) for l in SC.DEVICE_PAUSE_LEDGER.read_text().splitlines() if l.strip()]
    refused = [r for r in rows if r.get("action") == "already-paused"]
    check("the refused pause is recorded once", len(refused), 1)
    check("...naming the guard and the holder", (refused[0].get("who"), refused[0].get("pause_who")) if refused else None,
          ("thinkpad-guard", "jas"))

    resumed = TG._resume_own_pause(10.0, now=time.time() + 3600 * 24)
    check("the guard does not resume a pause that is not its own", resumed, False)
    check("the ThinkPad is still paused by Jas", SC.paused_devices().get("thinkpad", {}).get("who"), "jas")

    SC.pause_device("thinkpad", "jas", "paused again")
    check("the same holder may refresh its own pause", SC.paused_devices().get("thinkpad", {}).get("why"), "paused again")

    check("Resume clears it and says whose it was", SC.resume_device("thinkpad", who="jas"), "jas")
    check("the ThinkPad is running again", "thinkpad" in SC.paused_devices(), False)

    SC.pause_device("thinkpad", "thinkpad-guard", "RAM low")
    check("with nothing held, the guard's pause is taken", SC.paused_devices().get("thinkpad", {}).get("who"),
          "thinkpad-guard")

print(f"{'FAILED' if fails else 'ok'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
