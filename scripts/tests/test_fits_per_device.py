#!/usr/bin/env python3
"""fits() charges a model only against the models on its OWN device.

2026-10-12: the ThinkPad's Q4 (17.5 GB in its own 24 GB VRAM) was summed with Swift's 95 GB
against the GX10's single 108 GB budget, so every job routed to the ThinkPad was refused.
Hermetic: the footprint and device maps are set here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import dispatch_loop as d  # noqa: E402

d.FOOTPRINT_GIB = {"swift": 95.0, "big": 30.0, "tp": 17.5, "tp2": 17.0}
d.DEVICE_OF = {"swift": "gx10", "big": "gx10", "tp": "thinkpad", "tp2": "thinkpad"}
d.DEVICE_BUDGET_GIB = {"thinkpad": 22.5}
d.MEMORY_BUDGET_GIB = 108.0
cases = [
    ("a ThinkPad model beside Swift fits (different devices)", d.fits("tp", {"swift"}), True),
    ("Swift beside a ThinkPad model fits", d.fits("swift", {"tp"}), True),
    ("two GX10 models over its budget do not fit", d.fits("big", {"swift"}), False),
    ("two ThinkPad models over its 22.5 GB do not fit", d.fits("tp2", {"tp"}), False),
    ("an unknown model is charged to the GX10 at the unknown footprint", d.fits("nope", {"swift"}), False),
]
bad = 0
for name, got, want in cases:
    ok = got == want
    bad += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": got {got}"))
print(f"\n{len(cases) - bad} passed, {bad} failed")
sys.exit(1 if bad else 0)
