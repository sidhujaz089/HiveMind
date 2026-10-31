#!/usr/bin/env python3
"""A review backlog may delay reviews; it may not starve the production line.

t9530, 2026-10-28. The dispatcher skipped 161 READY chunks as "waiting -- every model has a lane" in a
tick that reported running: 0, while model_slots("swift") was 4. The lanes had been given to PLANNED
reviews: plan() offered one `free` list to both the review pass and production, and the review backlog
drained it. Production built nothing for an hour while the Hub faithfully showed "154 ready to build".

The rule this pins: while a build is ready, the review pass may never take the LAST lane.

RCA: workspace/rca/rca-lane-starvation-2026-10-28.md
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

    def chunk(n, kind):
        return {"chunk": f"{kind}-{n}", "brief": f"b{n}.txt", "brief_sha256": f"sha{n}",
                "files": "scripts/x.py", "target": "scripts/tests/test_x.py"}

    # classify/failures/checkpoint/fits are the dispatcher's inputs; this test supplies them so the LANE
    # arithmetic is what is under test, not the whole pipeline.
    real = (D.classify, D.failures, D.checkpoint_model, D.fits)
    # THE GLOBAL CAP IS NOT WHAT IS UNDER TEST: task_routing.max_lanes(ROUTING, "production") is the
    # Building responsibility's Max lanes, and the live store sets it to 1 -- which caps the whole box at
    # one build at a time whatever the slots. A test of the LANE ARITHMETIC must not be re-capped by a
    # setting from the live role store, so it is neutralised here and restored after.
    real_cap = D.task_routing.max_lanes
    D.task_routing.max_lanes = lambda *a, **k: 0
    try:
        D.failures = lambda *a, **k: []
        D.checkpoint_model = lambda b: None
        D.fits = lambda m, busy: True

        def plan_with(reviews, ready, lanes):
            D.classify = lambda chunks, running, red_of, history, objections: (
                [chunk(i, "rev") for i in range(reviews)],
                [chunk(i, "build") for i in range(ready)], [])
            return D.plan(["swift"], [], [], lambda t: False, reviewer="swift",
                          slots=lambda m: lanes)

        starts, skipped, free = plan_with(reviews=3, ready=1, lanes=1)
        kinds = [s["kind"] for s in starts]
        check("with one lane and a build waiting, the BUILD takes it (not a review)",
              kinds == ["production"], str(kinds))
        check("  and the reviews say a lane is held for production",
              any("held for production" in str(w) for _, w in skipped), str(skipped[:2]))

        starts, skipped, _ = plan_with(reviews=3, ready=0, lanes=1)
        check("with no build waiting, reviews proceed as before",
              [s["kind"] for s in starts] == ["preflight"], str([s["kind"] for s in starts]))

        starts, _, _ = plan_with(reviews=2, ready=1, lanes=2)
        kinds = sorted(s["kind"] for s in starts)
        check("with two lanes: one review AND one build", kinds == ["preflight", "production"], str(kinds))

        starts, _, _ = plan_with(reviews=5, ready=2, lanes=1)
        check("one lane still cannot start more builds than it has lanes",
              [s["kind"] for s in starts].count("production") == 1, str([s["kind"] for s in starts]))
    finally:
        D.classify, D.failures, D.checkpoint_model, D.fits = real
        D.task_routing.max_lanes = real_cap

    print("test_production_keeps_a_lane:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
