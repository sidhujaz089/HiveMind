#!/usr/bin/env python3
"""R1: the lane count comes from the MEASURED curve, from the engine's ceiling, and never from a stored 4.

Jas, 2026-10-29: "What got us here won't get us there." The stored number is the engine's `--max-num-seqs`, not a
capacity decision, and the measured curve on this box is NOT monotonic: 1/2/4 lanes gave 11.43/18.90/25.29 tok/s
aggregate at ~3% pressure, and 20.12/35.82/35.31 at ~49%. Four lanes is right at one pressure and wasted at another,
so a stored constant cannot be right at all three.

THE SWITCH IS SHARED WITH THE BYTE GATE, deliberately: "the redesign's admission layer is on" must be one statement,
not two that can disagree. While it is off -- the default -- model_slots returns exactly what it always did, which is
what makes this reversible.
"""
import os
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import dispatch_loop as D
    import capacity_model as C
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    D._MEASURED_SLOTS.clear()
    # OFF: the old behaviour, and the curve is never even consulted.
    with mock.patch("capacity_model.gate_switch_on", return_value=False):
        with mock.patch("capacity_model.inference_lanes", side_effect=AssertionError("consulted while OFF")):
            off = D.model_slots("swift")
    check("gate OFF: the stored slots decide and the curve is NOT consulted", isinstance(off, int) and off > 0,
          str(off))

    # ON: the measured best wins.
    D._MEASURED_SLOTS.clear()
    with mock.patch("capacity_model.gate_switch_on", return_value=True), \
         mock.patch("capacity_model.inference_lanes", return_value=(2, "measured best 2 at 35.82 tok/s")):
        on = D.model_slots("swift")
    check("gate ON: the measured best (2) replaces the stored number", on == 2, str(on))

    # ON with no curve: the ENGINE's ceiling, stated as UNPROVEN, never a guess of 1.
    D._MEASURED_SLOTS.clear()
    with mock.patch("capacity_model.gate_switch_on", return_value=True):
        n, why = C.inference_lanes(slots_max=4, curve={})
    check("no curve: the ceiling with UNPROVEN, not a guess", n == 4 and "UNPROVEN" in why, f"{n} {why}")

    # ON and the curve says more than the engine allows: the ceiling wins.
    D._MEASURED_SLOTS.clear()
    with mock.patch("capacity_model.gate_switch_on", return_value=True), \
         mock.patch("capacity_model.inference_lanes", return_value=(4, "capped")):
        check("the engine's ceiling is honoured (a 4-lane answer is still a number)",
              D.model_slots("swift") == 4, "")

    # UNREADABLE: fall back to the stored slots and SAY so -- never silently to 1.
    D._MEASURED_SLOTS.clear()
    with mock.patch("capacity_model.gate_switch_on", side_effect=RuntimeError("store gone")):
        got = D.model_slots("swift")
    check("an unreadable capacity model falls back to the stored slots", isinstance(got, int) and got > 0, str(got))

    src = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    check("the slot decision names the redesign rule in the code, for the next reader",
          "R1, MEASURED CAPACITY" in src, "")
    print("test_capacity_replaces_model_slots:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
