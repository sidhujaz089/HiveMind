#!/usr/bin/env python3
"""R4: once the BYTE gate decides admission, PSI annotates instead of refusing. While it is off, nothing changes.

MEASURED 2026-10-29, and this is why the rule exists: a tick reported "20.4 GB free" against a 16 GB build need --
bytes ADMITTED the build -- and refused it anyway because PSI had been 73% over the last minute. PSI on this box
measures the stall of getting SWAPPED pages back (26 GB of a 48 GB swap was in use), not "there is no room". As a
latch it starves the line for hours; as a number it tells a reader something true.

THE CHANGE IS REVERSIBLE BY CONSTRUCTION: with the byte gate OFF (the default) pressure still refuses exactly as it
did, so the old behaviour is one switch away. That is the property these checks pin.
"""
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import dispatch_loop as D
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    src = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    check("the tick asks whether the BYTE gate is on", "_gated_by_bytes = admission_gate_on()" in src, "")
    check("  and consults pressure ONLY when it is off",
          "_psi = pressure_hold() if (build_ready and not _gated_by_bytes) else None" in src, "")
    check("  and says so out loud when it annotates", "annotated, not refused" in src, "")

    # THE SWITCH IS THE BYTE GATE'S OWN, so the two cannot disagree.
    with mock.patch("capacity_model.gate_switch_on", return_value=True, create=True):
        check("gate ON  -> admission_gate_on() is True", D.admission_gate_on() is True, "")
    with mock.patch("capacity_model.gate_switch_on", return_value=False, create=True):
        check("gate OFF -> admission_gate_on() is False", D.admission_gate_on() is False, "")

    # AND AN UNREADABLE SWITCH KEEPS THE OLD BEHAVIOUR rather than deciding for the operator.
    import capacity_model
    real = capacity_model.gate_switch_on
    capacity_model.gate_switch_on = mock.Mock(side_effect=RuntimeError("store gone"))
    try:
        got = D.admission_gate_on()
    finally:
        capacity_model.gate_switch_on = real
    check("an unreadable switch falls back to pressure gating (never to silence)", got is False, str(got))

    # THE OLD BEHAVIOUR IS UNCHANGED: with a hold and no byte gate, a ready build is still refused.
    ok, hold, _w = D.pressure_build_gate({"p1": 1}, "73% over the last minute", patience=1,
                                         wait=lambda s: (False, 0))
    check("with pressure gating, a ready build is still refused and the hold stands",
          ok == {} and hold == "73% over the last minute", f"{ok!r} {hold!r}")
    print("test_pressure_is_a_warning_not_a_gate:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
