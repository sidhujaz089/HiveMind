#!/usr/bin/env python3
"""The ThinkPad eGPU cycle (Jas, 2026-10-14): the Auditor hands over at 01:00 only if builds are ready,
only in the night window; Frontline hands back when nothing is ready and nothing runs; a switch drains,
then unloads, then turns the new role on -- never loading beside the old one. decide() only; pure."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import thinkpad_cycle as C  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def local(h, m=0, day=14):
    return time.mktime((2026, 10, day, h, m, 0, 0, 0, -1))


aud = {"mode": "auditor", "phase": "on", "since": local(9, day=13)}
report("no state: start", C.decide({}, local(12), 5, 0)[0], "start")
report("Auditor before 01:00: stays", C.decide({**aud, "since": local(9)}, local(23), 5, 0)[0], "stay")
report("01:30 with builds ready: drain to Frontline", C.decide(aud, local(1, 30), 5, 0), ("drain", "frontline"))
report("01:30 with nothing ready: the Auditor keeps it", C.decide(aud, local(1, 30), 0, 0)[0], "stay")
report("the next afternoon a ready build does NOT switch it", C.decide(aud, local(15), 3, 0)[0], "stay")
fr = {"mode": "frontline", "phase": "on", "since": local(1, 30)}
report("Frontline with builds ready: stays", C.decide(fr, local(3), 4, 0)[0], "stay")
report("Frontline, nothing ready but a build running: stays", C.decide(fr, local(3), 0, 1)[0], "stay")
report("Frontline, nothing ready, nothing running: drain to the Auditor", C.decide(fr, local(3), 0, 0),
       ("drain", "auditor"))
dr = {"mode": "auditor", "from": "frontline", "phase": "draining", "since": local(3)}
report("draining with old jobs running: waits, nothing loads", C.decide(dr, local(3, 5), 0, 2)[0], "stay")
report("drained: unload the old model", C.decide(dr, local(3, 10), 0, 0), ("unload", "frontline"))
report("unloaded: the new role goes on", C.decide({**dr, "phase": "unloaded"}, local(3, 11), 0, 0), ("on", "auditor"))
report("the off switch exists", C.OFF.name, "thinkpad-cycle-off")
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
