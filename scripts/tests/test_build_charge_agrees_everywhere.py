#!/usr/bin/env python3
"""R3's wiring: ONE number for what a build is charged, used by the booking, the admission and the gate alike.

The measured distribution is in capacity_model (p95 8.06 GB from 56,001 peaks, against a 12.0 GB constant that sits
above the 99th percentile). This pins the WIRING, and the invariant that matters is agreement: if the booking charged
one figure and the gate refused on another, the ledger would record a promise the scheduler never enforced -- the
two-sources-of-truth fault this whole redesign exists to remove. So the number is computed in ONE place and every
caller asks it.

While the admission layer is off, the constant stands and nothing about the scheduler changes.
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

    D._CHARGE.clear()
    with mock.patch("capacity_model.gate_switch_on", return_value=False), \
         mock.patch("capacity_model.sized_build_gib", side_effect=AssertionError("consulted while OFF")):
        off, why = D.build_charge_gib()
    check("layer OFF: the constant stands and the measured rule is never consulted", off == 12.0, f"{off} {why}")

    D._CHARGE.clear()
    with mock.patch("capacity_model.gate_switch_on", return_value=True), \
         mock.patch("capacity_model.sized_build_gib", return_value=(8.1, "p95 of 56,001 peaks is 8.06 GB")):
        on, why2 = D.build_charge_gib()
    # A0 (2026-10-30) RETRACTS THIS ONE CASE. The brief ordered the charge back to the constant with the layer
    # left ON, because the measured figure's inputs were found to be 20-second SAMPLES of a group that also counts
    # test clones -- neither a per-build peak nor reliably build memory. So layer ON now means: the layer gates,
    # and the CHARGE is still the constant until capacity_model.per_build_peaks() has data to re-derive it.
    check("layer ON: the charge is STILL the constant until per-build peaks exist (A0)", on == 12.0, f"{on} {why2}")
    check("  and the reason says the measured charge is not in force, and why",
          "not in force" in why2.lower() and "per_build_peaks" in why2, why2)
    check("  and it names the evidence that disqualified the measured rule, rather than quoting a percentile",
      "p95" not in why2 and "samples" in why2, why2)

    D._CHARGE.clear()
    with mock.patch("capacity_model.gate_switch_on", return_value=True), \
         mock.patch("capacity_model.sized_build_gib", return_value=(8.1, "measured")):
        first = D.build_charge_gib()[0]
        second = D.build_charge_gib()[0]
    check("the charge is computed once per process, so admission cannot drift mid-tick", first == second, "")

    D._CHARGE.clear()
    with mock.patch("capacity_model.gate_switch_on", side_effect=RuntimeError("store gone")):
        got, why3 = D.build_charge_gib()
    check("an unreadable capacity model falls back to the constant and says so",
          got == 12.0 and "constant" in why3, f"{got} {why3}")

    # AGREEMENT: no caller may charge its own figure. Every former use of the constant goes through the function.
    src = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    raw = [l.strip() for l in src.splitlines()
           if "BUILD_GIB" in l and "build_charge_gib" not in l and not l.strip().startswith("#")
           and not l.strip().startswith('"""')]
    allowed = ("BUILD_GIB = 12.0", "return (float(BUILD_GIB),", "R3, FROM THE MEASURED PEAKS")
    offenders = [l for l in raw if not any(l.startswith(a) for a in allowed)]
    check("every admission and booking site asks the one function", not offenders, str(offenders)[:200])
    check("  and the booking and the gate are the same expression",
          "host_gb = (build_charge_gib()[0] + ADMIT_MARGIN_GIB)" in src
          and "_cm.admission_gate(job[\"kind\"], _free, build_charge_gib()[0] + ADMIT_MARGIN_GIB" in src, "")
    print("test_build_charge_agrees_everywhere:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
