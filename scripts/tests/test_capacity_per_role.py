#!/usr/bin/env python3
"""Capacity belongs to the ROLE, not the model (t9537).

Jas, 2026-10-28: "When a role has capacity the scheduler should give it work from the top of the queue
based on that model's responsibilities configuration ... If I swap Swift for a new model, I dont want
the Manager role to break."

The defect, measured at tick 14:58Z: running 0, 121 priority items "queued: swift is doing priority
work", 174 builds skipped "waiting -- every model has a lane". schedule() reserved the WHOLE model
whenever priority work was queued for it, even when the role's Max lanes left lanes nobody could use,
and production was offered only models not in `reserved`.

  a. a role with 4 lanes and 2 priority jobs (at that task's Max lanes) offers 2 lanes to production
  b. a role with 1 lane and 1 priority job offers 0, and the skip reason names the role and the task
  c. swapping the model on a role changes nothing about the work it is offered
  d. deleting the role and adding a new one holding the same tasks gives the same assignment

Red first: (a) and (b) fail against the code before t9537.
"""
import copy
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import dispatch_loop as DL  # noqa: E402
import task_routing  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED, FAILED = PASSED + ok, FAILED + (not ok)
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f" (want {want!r})"))


def store(role="line", name="Line", lanes=4, rca_cap=2):
    """One model role holding Building and RCA -- the live shape, with the names made up on purpose."""
    return {"roles": {role: {"name": name, "kind": "model", "lanes": lanes,
                             "tasks": [{"task": "rca", "max_lanes": rca_cap, "interrupt": False},
                                       {"task": "building", "max_lanes": 0, "interrupt": False}]}}}


def offered(model, lanes, d, running, items):
    """Lanes production is offered on `model` after the priority pass -- what the tick hands plan()."""
    DL.ROUTING = d
    starts, _pre, queued, reserved = DL.schedule([model], items, running, slots=lambda m: lanes)
    if model in reserved:
        return 0, starts, queued
    used = sum(1 for r in running if r["model"] == model) + sum(1 for s in starts if s["model"] == model)
    return max(0, lanes - used), starts, queued


def jobs(model, role, n):
    return [{"model": model, "kind": "rca", "role": role, "pid": 100 + i, "key": f"r{i}", "elapsed_s": 60}
            for i in range(n)]


def waiting(model, role, n):
    return [{"model": model, "kind": "rca", "role": role, "key": f"w{i}", "level": 2, "created": ""}
            for i in range(n)]


saved = DL.ROUTING
try:
    # a. 4 lanes, 2 RCAs running (RCA's Max lanes is 2), more RCAs waiting: two lanes are production's.
    n, starts, queued = offered("m1", 4, store(), jobs("m1", "line", 2), waiting("m1", "line", 5))
    report("a. 4 lanes + 2 priority jobs at their cap offers 2 to production", n, 2)
    report("   and no third RCA starts past its Max lanes", starts, [])

    # b. 1 lane, 1 RCA running: nothing for production, and the build's skip names the role and task.
    n, _s, _q = offered("m1", 1, store(lanes=1), jobs("m1", "line", 1), waiting("m1", "line", 1))
    report("b. 1 lane + 1 priority job offers 0", n, 0)
    chunk = {"chunk": "c1", "brief": "b1.txt", "brief_sha256": "x"}
    real = (DL.classify, DL.failures, DL.checkpoint_model)
    DL.classify = lambda *a, **k: ([], [chunk], [])
    DL.failures = lambda *a, **k: []
    DL.checkpoint_model = lambda *a, **k: None
    try:
        DL.ROUTING = store(lanes=1)
        _st, skipped, _idle = DL.plan([], [chunk], [], {}, reviewer=None)
        why = skipped[0][1] if skipped else ""
        report("   the skip reason names the ROLE", "Line" in why, True)
        report("   and the responsibility", "Building" in why, True)
        report("   and never 'every model has a lane'", "every model" in why, False)
        DL.ROUTING = store(role="crew", name="Crew", lanes=1)
        _st, skipped, _idle = DL.plan([], [chunk], [], {}, reviewer=None)
        report("   a renamed role is named with no code edit", "Crew" in (skipped[0][1] if skipped else ""), True)
    finally:
        DL.classify, DL.failures, DL.checkpoint_model = real

    # c. the same role on a different model: the same work is offered.
    base = offered("m1", 4, store(), jobs("m1", "line", 2), waiting("m1", "line", 5))
    swap = offered("m2", 4, store(), jobs("m2", "line", 2), waiting("m2", "line", 5))
    report("c. swapping the model changes nothing offered",
           (swap[0], [s["key"] for s in swap[1]]), (base[0], [s["key"] for s in base[1]]))

    # d. delete the role, add a new one holding the same two tasks: the same assignment.
    d2 = store(role="crew", name="Crew")
    new = offered("m1", 4, d2, jobs("m1", "crew", 2), waiting("m1", "crew", 5))
    report("d. a new role with the same tasks gets the same assignment",
           (new[0], [s["key"] for s in new[1]]), (base[0], [s["key"] for s in base[1]]))
    report("   and the scoping/building owner follows the store", task_routing.owner(d2, "building"), "crew")
finally:
    DL.ROUTING = saved

print(f"\n{PASSED} passed, {FAILED} failed")
print("test_capacity_per_role:", "ok" if not FAILED else "FAILED")
sys.exit(1 if FAILED else 0)
