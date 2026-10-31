#!/usr/bin/env python3
"""Priority follows each ROLE's own Responsibilities order, and the two ways that silently breaks are checks.

t9540 R4 (Jas 2026-10-28: "A role serves its responsibilities top-down... Two roles may order the same task
differently, and each follows its own order. Replace any global or per-kind priority in schedule()/plan()
with each role's order") and Claude's decision 3.

Three properties, each pinned because each fails silently:
  1. two roles with OPPOSITE orders get OPPOSITE picks -- if a global table decided, they would get the same;
  2. re-ordering a role on the Models tab changes its pick with NO code edit;
  3. a kind of work NO role holds, and a responsibility that waits while the box works, are both FAILURES
     rather than quiet queues. The second is the shape that built nothing for a day on 2026-10-28.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def store(order_a, order_b):
    """Two roles on one model, each with its own responsibility order."""
    return {"roles": {
        "roleA": {"name": "A", "kind": "model", "model": "swift", "lanes": 2,
                  "tasks": [{"task": t, "interrupt": False} for t in order_a]},
        "roleB": {"name": "B", "kind": "model", "model": "swift2", "lanes": 2,
                  "tasks": [{"task": t, "interrupt": False} for t in order_b]},
    }, "_escalation": {"vision": 1, "technical": 2, "rework": 3}}


def main():
    import task_routing as T
    import check_workspace as C
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    items = [{"kind": "rca", "role": "roleA", "key": "r1"},
             {"kind": "script-review", "role": "roleA", "key": "s1"}]
    # ROLE A: rca first. ROLE B: script-review first. One lane each, so each gets ONE pick.
    a = store(["rca", "script_review"], ["script_review", "rca"])
    picked, _ = T.round_robin(a, items, {}, 1)
    check("one lane to role A takes ITS first responsibility (rca)", [p["kind"] for p in picked] == ["rca"],
          [p["kind"] for p in picked])

    # THE SAME LANE, THE OPPOSITE ORDER ON THE STORE: the pick follows, with no code change.
    b = store(["script_review", "rca"], ["rca", "script_review"])
    picked2, _ = T.round_robin(b, items, {}, 1)
    check("  re-ordering the role on the Models tab changes its pick, with no code edit",
          [p["kind"] for p in picked2] == ["script-review"], [p["kind"] for p in picked2])

    # 3a. A KIND NOBODY HOLDS.
    nobody = store(["rca"], ["script_review"])
    missing = C.unheld_kinds(nobody)
    check("a kind no role holds is reported, not queued silently",
          "preflight" in missing and "building" in missing, str(missing))
    held = store(["rca", "script_review", "preflight", "building", "console", "scoping",
                  "supervision", "session_review", "script_audit", "done_check"], [])
    check("  and a fully held store reports none", C.unheld_kinds(held) == [], str(C.unheld_kinds(held)))

    # 3b. STARVATION, ON ONE TICK -- the only tick the record keeps (dispatch-tick-latest.json).
    def tick(n_wait, started):
        return {"at": "2026-10-28T23:19:20Z", "started": [{"kind": "rca"}] * started,
                "skipped": [{"chunk": f"p{i}", "why": "waiting for a free lane on Manager (Building)"}
                            for i in range(n_wait)]}

    check("a tick that starts work WHILE chunks wait for a lane is the starving shape",
          C.tick_waiting(tick(5, 1)) == [("waiting for a free lane on Manager (Building)", 5)],
          str(C.tick_waiting(tick(5, 1))))
    check("  an idle tick with the same waiting is the OTHER finding, not this one",
          C.tick_waiting(tick(5, 0)) == [("waiting for a free lane on Manager (Building)", 5)], "")
    check("  and nothing waiting is nothing to report", C.tick_waiting(tick(0, 3)) == [], "")

    # 3c. THE UNHELD-KIND CHECK FIRES ONLY WHEN WORK OF THAT KIND IS WAITING (a taxonomy fact with nothing
    # waiting is not a fault: console/done_check/supervision are dispatched by their own paths).
    store_no_console = store(["rca", "script_review"], [])
    check("an unheld kind with nothing waiting is not a failure",
          C.unheld_kinds(store_no_console, {"queued": [], "skipped": []}) == [],
          str(C.unheld_kinds(store_no_console, {"queued": [], "skipped": []})))
    waiting_tick = {"queued": [{"kind": "console", "key": "c1"}], "skipped": []}
    check("  but an unheld kind WITH work waiting is",
          C.unheld_kinds(store_no_console, waiting_tick) == ["console"],
          str(C.unheld_kinds(store_no_console, waiting_tick)))

    print("test_priority_follows_responsibilities:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
