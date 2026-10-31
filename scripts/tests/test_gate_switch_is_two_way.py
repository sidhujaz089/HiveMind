#!/usr/bin/env python3
"""A switch that can only be turned ON is not a switch (2026-10-30).

capacity_model.gate_switch_on() decides whether the admission gate judges every launch. Its docstring says "OFF by
default, and OFF on any doubt", but an explicit 0 in HIVEMIND_ADMISSION_GATE fell through to the FILE -- so on a box
where workspace/run-logs/admission-gate.enabled exists there was no way to turn the gate off except deleting the flag.

What that cost: THREE suites were red on main and none was about the rule it tested. test_dispatch_duplicate_guard (the
duplicate guard), test_dispatch_pressure_hold (the pressure hold) and test_fast_path (the fast path) were all refused
earlier by "admission UNPROVEN: ... the slot count is unreadable", because a sandbox cannot read a slot count and the
suite had no way to say "not in this regime". Two branches could not land.

RED ON MAIN: gate_switch_on({GATE_ENV: "0"}) returns True while the file exists.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import capacity_model as cm          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


import tempfile
with tempfile.TemporaryDirectory() as td:
    on = Path(td) / "gate.enabled"
    on.write_text("{}", encoding="utf-8")
    off = Path(td) / "absent.enabled"

    check("a present file turns the gate ON", cm.gate_switch_on({}, on) is True, "file present")
    check("an absent file leaves it OFF", cm.gate_switch_on({}, off) is False, "file absent")
    for v in ("0", "false", "no", "off", "FALSE", " off "):
        check(f"an explicit {v!r} turns it OFF even with the file present",
              cm.gate_switch_on({cm.GATE_ENV: v}, on) is False, f"{v!r} was ignored")
    for v in ("1", "true", "yes", "on", "TRUE"):
        check(f"an explicit {v!r} turns it ON", cm.gate_switch_on({cm.GATE_ENV: v}, off) is True, v)
    check("a value it does not understand falls back to the file",
          cm.gate_switch_on({cm.GATE_ENV: "maybe"}, on) is True, "maybe")

print("test_gate_switch_is_two_way:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
