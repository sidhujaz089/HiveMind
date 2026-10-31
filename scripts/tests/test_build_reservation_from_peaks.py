#!/usr/bin/env python3
"""R3: a build is charged for its MEASURED peak, not a constant -- and the constant stands when there is no data.

The number this replaces was never evidence-free: BUILD_GIB 12.0 plus the 4 GB admission margin meant a build needed
16 GB free. Measured over 56,004 recorded peaks from memory-watch (p50 2.99, p90 6.79, p95 8.06, p99 11.19 GB), 12.0
sits ABOVE the 99th percentile, so the box was reserving 16 GB for a build whose 95th-percentile peak is 8.06 -- four
gigabytes of a six-gigabyte slack, spent on a shape builds do not have.

The rule takes a HIGH PERCENTILE rather than the maximum because the `job:build` group is the SUM of concurrent
builds: the all-time maximum of 52.84 GB is several at once, not one build. It caps at the current constant, so a
measurement may lower the number and never quietly raise it.
"""
import sys
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

    # THE REAL DISTRIBUTION, in miniature: mostly small builds, a few large, one aggregate outlier.
    real = [2.99] * 50 + [6.79] * 40 + [8.06] * 5 + [11.19] * 4 + [52.84]
    gib, why = C.sized_build_gib(real)
    check("the charge follows the measured p95, not the constant", 7.5 <= gib <= 8.5, f"{gib} {why}")
    check("  and the outlier does NOT set it (the group is a sum of concurrent builds)", gib < 11.19, str(gib))
    check("  the reason names the percentile and the sample size", "p95" in why and "100" in why, why)

    gib2, why2 = C.sized_build_gib([3.0] * 100)
    check("a small measured shape lowers the charge", gib2 < 12.0, f"{gib2} {why2}")

    gib3, why3 = C.sized_build_gib([40.0] * 100)
    check("and it can never rise above the constant it replaced", gib3 == 12.0, f"{gib3} {why3}")

    gib4, why4 = C.sized_build_gib([])
    check("NO DATA keeps the constant and says UNPROVEN", gib4 == 12.0 and "UNPROVEN" in why4, why4)

    gib5, _ = C.sized_build_gib([1.0] * 100)
    check("a floor stops a thin shape under-reserving", gib5 >= 6.0, str(gib5))

    check("percentiles are nearest-rank and survive tiny samples",
          C.percentile([1.0], 0.95) == 1.0 and C.percentile([], 0.95) is None and C.percentile([1, 2, 3, 4], 0.5) == 3,
          "")
    print("test_build_reservation_from_peaks:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
