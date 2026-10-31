"""dispatch_loop.pressure_hold / memory_admit: no NEW build starts while the one-minute memory pressure is
high (on at PRESSURE_HOLD_ON, off under PRESSURE_HOLD_OFF); other work and running builds are untouched."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import dispatch_loop as DL  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


# WHICH REGIME THIS SUITE IS ABOUT (2026-10-30). These cases test the PRESSURE hold: that a build is skipped with a
# reason when pressure says wait. On a box with workspace/run-logs/admission-gate.enabled the byte gate judges
# admission first and ANNOTATES the pressure instead of refusing ("the byte gate decides admission"), so the
# expectation below is only true in the ungated regime -- say so, rather than let a machine's flag decide it.
_prev_gate = os.environ.get("HIVEMIND_ADMISSION_GATE")
os.environ["HIVEMIND_ADMISSION_GATE"] = "0"

st = Path(tempfile.mkdtemp()) / "hold.json"
on, off = DL.PRESSURE_HOLD_ON, DL.PRESSURE_HOLD_OFF
report("calm: no hold", DL.pressure_hold(0.0, st), None)
report("at the on-line: held", DL.pressure_hold(on, st) is not None, True)
report("between the lines, after a hold: still held", DL.pressure_hold((on + off) / 2, st) is not None, True)
report("under the off-line: released", DL.pressure_hold(off - 0.1, st), None)
report("between the lines, after a release: not held", DL.pressure_hold((on + off) / 2, st), None)

real = DL.pressure_hold
DL.pressure_hold = lambda *a, **k: "waiting for memory pressure: test"
try:
    starts = [{"chunk": "p1", "kind": "production"}, {"chunk": "r1", "kind": "rca"}]
    kept, skipped = DL.memory_admit(starts, [], avail=200.0, pending=0)
    report("a pure decision (avail given) never reads live pressure", [c for c, _ in skipped], [])
    real_mem = DL.mem_available_gib
    DL.mem_available_gib = lambda *a: 200.0
    try:
        kept, skipped = DL.memory_admit(starts, [], pending=0)
    finally:
        DL.mem_available_gib = real_mem
    report("held (live call): the build is skipped, with the reason", [c for c, _ in skipped], ["p1"])
    report("  and other work still starts", [j["chunk"] for j in kept], ["r1"])
finally:
    DL.pressure_hold = real
DL.pressure_hold = lambda *a, **k: None
try:
    kept, skipped = DL.memory_admit([{"chunk": "p1", "kind": "production"}], [], avail=200.0, pending=0)
    report("not held and memory free: the build starts", [j["chunk"] for j in kept], ["p1"])
finally:
    DL.pressure_hold = real
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
