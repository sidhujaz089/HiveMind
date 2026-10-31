#!/usr/bin/env python3
"""A skipped build names the RESOURCE it waits on -- never "a lane", which cannot be checked.

Redesign step 4 (workspace/rca/scheduler-audit-2026-10-29.md). "Waiting for a free lane" was printed 169 times in
a single tick on 2026-10-28 whose real constraint was host memory: a "lane" is an integer in the role store, not
something the box measures, so the sentence sent every reader to the wrong fix -- including me, twice.

The replacement names the jobs holding slots and the slots they hold, both counted from the planner's own inputs,
so the sentence can be checked against the same numbers the decision used.
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

    # The phrase is gone from what the planner WRITES. It survives in one place only: as a match pattern for
    # ticks written before this change, which is why the legacy constant exists.
    src = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    check("the planner no longer WRITES the phrase 'free lane'",
          'f"{LANE_WAIT}' not in src and "LANE_WAIT}" not in src, "the old wording is still emitted")
    check("  it names the slots instead, counted from the plan's own inputs",
          # 2026-10-29: a MODEL shortage and a SLOT shortage are now different sentences, because saying
          # "no free sequence slot" when no model was free sent readers to the wrong number for a day.
          "all taken by" in src and "no free model for this build" in src, "")
    check("  and the constant is a stable prefix other code can match", D.SLOT_WAIT.startswith("no free sequence slot"),
          D.SLOT_WAIT)
    check("  old ticks are still understood by the hold relabel", D.LEGACY_LANE_WAIT in src,
          "ticks from before the change would stop being relabelled")

    # END TO END: a plan with no free lane produces the new sentence, not the old one.
    import task_routing as T
    chunk = {"chunk": "p1", "brief": "b.txt", "brief_sha256": "s", "files": "scripts/x.py",
             "target": "scripts/tests/test_x.py"}
    real = (D.classify, D.failures, D.checkpoint_model, D.fits)
    try:
        D.classify = lambda *a, **k: ([], [chunk], [])
        D.failures = lambda *a, **k: []
        D.checkpoint_model = lambda b: None
        D.fits = lambda m, busy: True
        starts, skipped, _free = D.plan(["swift"], [], [{"model": "swift", "kind": "preflight", "pid": 1}],
                                       lambda t: False, reviewer=None, slots=lambda m: 1)
    finally:
        D.classify, D.failures, D.checkpoint_model, D.fits = real
    reasons = [str(w) for _n, w in skipped]
    # THE SHORTAGE NAMED MUST BE THE ONE THAT EXISTS (2026-10-29). This asserted the literal "no free sequence
    # slot", which is what a real plan printed WHILE the actual shortage was a missing model -- proven by planning
    # the same 176 chunks with slots=2 and slots=4 and getting IDENTICAL results. The property is that the reason
    # names a real resource, not that it names slots.
    check("a real plan with no free model says so, and never blames a lane",
          any(("no free model" in r) or ("no free sequence slot" in r) for r in reasons), str(reasons)[:220])
    check("  and NO skip reason contains 'free lane'", not any("free lane" in r for r in reasons),
          str(reasons)[:200])
    check("  and the reason carries the count it decided on",
          any(("model(s)" in r and "job(s)" in r) or "slot(s)" in r for r in reasons),
          str(reasons)[:200])
    # A MATCHER THAT ONLY KNOWS THE OLD PHRASE GOES BLIND the moment the wording changes -- both directions must
    # be understood, or the lane-contradiction and thinkpad checks silently stop firing.
    import check_workspace as C
    check("check_workspace matches the NEW wording as well as the old ones",
          "no free sequence slot" in str(C.LANE_WAIT_PREFIXES), str(C.LANE_WAIT_PREFIXES))
    check("  and thinkpad_cycle's counter does too",
          "no free sequence slot" in (ROOT / "scripts" / "thinkpad_cycle.py").read_text(encoding="utf-8"), "")
    print("test_skip_reasons_name_a_resource:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
