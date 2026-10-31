#!/usr/bin/env python3
"""How many lanes to run comes from a MEASURED curve and the engine's ceiling -- not from a stored 4.

Jas, 2026-10-29: "What got us here won't get us there." The stored `model_slots` = 4 is the engine's
`--max-num-seqs`, i.e. its ceiling, not a capacity finding. And the curve is not monotonic on this box:
measured with scripts/token_throughput.py (medians of three trials),
  while healthy:  1 lane 11.43, 2 lanes 18.90, 4 lanes 25.29 tok/s  -> more lanes, more throughput
  at ~65% pressure: 2 lanes 28.41 against 4 lanes 15.15 tok/s        -> the same shape INVERTED
So the answer must come from the numbers, and with no numbers the honest answer is UNPROVEN plus the ceiling.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import capacity_model as C
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    healthy = {1: 11.43, 2: 18.90, 4: 25.29}
    lanes, why = C.inference_lanes(slots_max=4, curve=healthy)
    check("the measured healthy curve picks 4 lanes", lanes == 4, f"{lanes} {why}")
    check("  and says it came from the measurement", "measured best" in why, why)

    inverted = {1: 11.43, 2: 28.41, 4: 15.15}
    lanes, why = C.inference_lanes(slots_max=4, curve=inverted)
    check("an INVERTED curve picks 2 lanes, not 4", lanes == 2, f"{lanes} {why}")

    lanes, why = C.inference_lanes(slots_max=2, curve=healthy)
    check("the engine's ceiling caps the measured best", lanes == 2 and "capped" in why, f"{lanes} {why}")

    lanes, why = C.inference_lanes(slots_max=4, curve={})
    check("NO CURVE is UNPROVEN plus the ceiling, never a guess",
          lanes == 4 and "UNPROVEN" in why and "--max-num-seqs" in why, f"{lanes} {why}")

    n, why = C.builds_that_fit(21.8, need_gb=16)
    check("builds fit by HOST BYTES: 21.8 GB free / 16 GB = 1", n == 1, f"{n} {why}")
    n, why = C.builds_that_fit(48.0, need_gb=16, cap=2)
    check("  and a cap still applies (Max lanes)", n == 2, f"{n} {why}")
    n, why = C.builds_that_fit(None)
    check("  unreadable host memory admits nothing and says UNPROVEN", n == 0 and "UNPROVEN" in why, why)

    # the curve file is read from the tool's own record
    d = Path(tempfile.mkdtemp()) / "curve.json"
    d.write_text('{"curve": {"1": 10.0, "2": 25.0, "4": 12.0}}')
    check("the curve file is read and parsed", C.load_curve(d) == {1: 10.0, 2: 25.0, 4: 12.0}, str(C.load_curve(d)))
    check("  the engine's ceiling is read from the running server, or defaults loudly",
          isinstance(C.engine_slots(), int) and C.engine_slots() > 0, str(C.engine_slots()))
    print("test_capacity_model:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
