#!/usr/bin/env python3
"""A0: the admission layer stays ON and the CHARGE goes back to 12 GB (2026-10-29 brief, 2026-10-30 evidence).

The brief: "A0 revert the charge to the 12 GB constant (one line in dispatch_loop.py's build_charge_gib), leaving
the layer on."

WHY IT IS STILL RIGHT, and stronger than when the brief was written. The 8.0 GB in force was derived from
capacity_model.sized_build_gib(), which takes the p95 of memory-watch's `job:build` group rows. Measured today:
  * those rows are 20-SECOND SAMPLES, not per-build peaks -- 56,826 samples against 1,051 builds ever started, each
    sample the SUM of whatever was building (A5);
  * the group itself counts ANY process whose cmdline holds coder.py, and the coder suites run coder.py as a
    fixture: the group was measured at 1.26 GB across 11 processes, every one a non-dispatched clone (A5 follow-up).
So the number is neither a peak nor reliably build memory. Until per-build peaks exist, the constant is the only
defensible charge.

RED ON MAIN: build_charge_gib() returns the measured 8.0 GB while the layer is on.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import dispatch_loop as DL          # noqa: E402
import capacity_model as cm         # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


# THE SWITCH IS AN UNTRACKED RUNTIME FILE, so a worktree reads it as OFF and a test that asks the box measures the
# checkout it is standing in, not the one that runs. Both states are injected here instead, which is also what pins
# the two halves of A0 independently: the layer stays ON, and the CHARGE is the constant.
_orig_switch, _orig_sized = cm.gate_switch_on, cm.sized_build_gib
try:
    cm.gate_switch_on = lambda *a, **k: True                      # the layer is ON
    cm.sized_build_gib = lambda *a, **k: (8.0, "p95 of recorded samples is 8.03 GB")   # and the measured charge exists
    gib, why = DL.build_charge_gib()
finally:
    cm.gate_switch_on, cm.sized_build_gib = _orig_switch, _orig_sized

check("the layer being ON does not turn the charge into the measured one", cm.gate_switch_on is _orig_switch,
      "the layer's own switch is untouched")

check("the charge is the 12 GB constant WHILE the layer is on and a measured charge exists",
      gib == DL.BUILD_GIB == 12.0, f"{gib} ({why})")
check("  and it says the measured charge is not in force", "constant" in why.lower(), why)
check("  and it does not quote a measured percentile", "p95" not in why.lower(), why)

check("the margin is unchanged, so a build is charged 16 GB in total",
      DL.ADMIT_MARGIN_GIB == 4.0 and gib + DL.ADMIT_MARGIN_GIB == 16.0, str(gib + DL.ADMIT_MARGIN_GIB))

# the measured path must still be reachable for the RE-derivation, not deleted
src = (HERE / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
check("sized_build_gib is still computed somewhere, so the charge can be re-derived later",
      "sized_build_gib" in src, "deleting the measurement would make the revert permanent by accident")
check("  and capacity_model still exposes it", hasattr(cm, "sized_build_gib"))
check("  and the per-build reader is still available for the re-derivation", hasattr(cm, "per_build_peaks"))

print("test_build_charge_reverted:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
