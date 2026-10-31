#!/usr/bin/env python3
"""A pressure dip smaller than a tick must still be usable -- the tick waits for it, bounded.

Jas, 2026-10-29: "if its eratic, can we raise the pressure threshold because it would add another lane and
high pressure is momentary and overall throughput will go up?" The instinct named a real defect; the fix is
not a higher threshold. MEASURED on this box: the hold released at 01:32:05Z when avg60 fell to 8.17%, and by
01:35Z it was back at 49% -- so the dip existed, lasted less than one tick, and NO build was admitted into it.
Raising the threshold would admit builds at 60%, where the same experiment measured 4 lanes at 15.15 tok/s
against 2 lanes at 28.41: more work, less throughput.

So the hold keeps its threshold and the tick waits for the dip instead: bounded by PATIENCE_S, re-reading
pressure a few times. This pins both directions and the bound.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import dispatch_loop as D
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    slept = []
    check("already released: no wait", D.wait_for_pressure_release(
        60, 5, read=lambda: None, sleep=slept.append) == (True, 0) and not slept, str(slept))

    # A DIP THAT OPENS ON THE THIRD READ is captured, having waited for it.
    reads = iter(["held", "held", None])

    def dip():
        return next(reads, None)

    ok, waited = D.wait_for_pressure_release(60, 5, read=dip, sleep=slept.append)
    check("a dip that opens within the window is USED", ok is True, f"ok={ok} waited={waited}")
    check("  and the wait is reported, not hidden", waited == 10, waited)

    # A HOLD THAT NEVER LIFTS GIVES UP AT THE BOUND.
    slept2 = []
    ok2, waited2 = D.wait_for_pressure_release(20, 5, read=lambda: "held", sleep=slept2.append)
    check("a hold that never lifts gives up at the bound", ok2 is False and waited2 == 20, f"{ok2} {waited2}")
    check("  and it really is bounded (no unbounded loop)", len(slept2) == 4, str(len(slept2)))

    check("the patience knob exists and is bounded by default",
          isinstance(D.PATIENCE_S, int) and 0 < D.PATIENCE_S <= 300, str(getattr(D, "PATIENCE_S", None)))

    src = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    # THE TICK CALLS THE GATE, and the gate is where "builds ready" and "the hold is the only blocker" are
    # decided -- asserting the tick's old inline shape would pin the very version that was broken.
    check("the tick decides through the gate", "pressure_build_gate(build_ready" in src, "")
    check("  and says PRESSURE when it worked, so the wait is visible", "PRESSURE  released after" in src, "")
    # THE OUTCOME, NOT THE WAIT (L1 review, 2026-10-29). The first version of this fix cleared the hold and then
    # let the NEXT line empty the ready builds, so no build could start and the wait bought a minute of nothing.
    # Its test called the wait on its own, so it passed while the feature did not work. These checks assert what
    # the tick will actually act on.
    ok4, hold4, waited4 = D.pressure_build_gate({"p1": 1}, "held", patience=1,
                                                wait=lambda s: (True, 5))
    check("A RELEASED HOLD KEEPS THE READY BUILDS -- the bug the first version had",
          bool(ok4) and hold4 is None, f"ready={ok4!r} hold={hold4!r}")
    check("  and it reports the wait it did", waited4 == 5, str(waited4))

    ok5, hold5, _w5 = D.pressure_build_gate({"p1": 1}, "held", patience=1, wait=lambda s: (False, 10))
    check("a hold that does NOT release drops the builds and stands", ok5 == {} and hold5 == "held",
          f"ready={ok5!r} hold={hold5!r}")

    ok6, hold6, _w6 = D.pressure_build_gate({}, "held", patience=1, wait=lambda s: (True, 5))
    check("no ready builds means nothing to wait for", ok6 == {} and hold6 == "held", f"{ok6!r} {hold6!r}")

    ok7, hold7, _w7 = D.pressure_build_gate({"p1": 1}, None, patience=1, wait=lambda s: (True, 5))
    check("no hold: the builds pass through untouched, no wait at all", ok7 == {"p1": 1} and hold7 is None,
          f"{ok7!r} {hold7!r}")

    src2 = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    gate_src = src2[src2.index("def pressure_build_gate"):]
    gate_src = gate_src[:gate_src.index("def pressure_hold")]
    check("the tick cannot empty the builds after a release: the clearing lives inside the gate",
          "return {}, str(held), waited" in gate_src, gate_src[:200])
    print("test_pressure_dip_is_used:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
