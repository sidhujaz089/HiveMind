#!/usr/bin/env python3
"""A build held back by memory says MEMORY, not "waiting for a free lane" (2026-10-28).

Tick 22:47Z: 168 builds skipped "waiting for a free lane on Manager (Building)" with 1 job on a
4-lane role. The tick had withheld the build lane: 16.6 GB free against a 12 + 4 GB admission.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dispatch_loop as DL  # noqa: E402

FAILED = 0


def check(name, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


# BOTH WORDINGS, because step 4 changed what the planner writes and ticks from before it still say the old form.
# A renamer that knew only one of them would leave a whole day of ticks saying "a free lane" while a hold was the
# real cause -- which is the defect this suite exists for.
lane = f"{DL.SLOT_WAIT} on Manager (Building)"
legacy = f"{DL.LEGACY_LANE_WAIT} on Manager (Building)"
sk = [("c1", lane), ("c2", "target green -- already on main")]
check("no hold: the slot reason stands", DL.name_build_hold(sk, None), sk)
got = DL.name_build_hold(sk, "16.6 GB free cannot admit a build")
check("a hold renames the slot wait", got[0][1], "waiting for memory: 16.6 GB free cannot admit a build")
check("and touches no other reason", got[1], sk[1])
leg = DL.name_build_hold([("c3", legacy)], "16.6 GB free cannot admit a build")
check("  and an OLD tick's lane wording is renamed too", leg[0][1],
      "waiting for memory: 16.6 GB free cannot admit a build")
src = (Path(DL.__file__)).read_text(encoding="utf-8")
check("the tick passes its hold to the renamer", "name_build_hold(skipped, build_hold)" in src, True)
print("test_build_hold_reason:", "ok" if not FAILED else "FAILED")
sys.exit(1 if FAILED else 0)
