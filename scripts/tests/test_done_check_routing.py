#!/usr/bin/env python3
"""t1135 item 1: "done_check" becomes a dispatcher kind a model role can hold.

Red on main: task_routing.KIND_TASK has no "done-check" entry (scripts/task_routing.py:45),
so both lookups below raise KeyError and "done_check" is absent from MODEL_TASKS (:55).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import task_routing  # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


STORE = {"roles": {
    "senior": {"kind": "model", "model": "swift:48b", "name": "Senior Swift",
               "tasks": [{"task": "done_check", "interrupt": False, "teamwork": False}]},
    "manager": {"kind": "model", "model": "q4", "name": "Manager Q4",
                "tasks": [{"task": "supervision", "interrupt": True, "teamwork": False}]},
}}
EMPTY = {"roles": {}}


def test_kind_and_task_are_linked_both_ways():
    check(task_routing.KIND_TASK.get("done-check") == "done_check",
          "KIND_TASK['done-check'] is %r, expected 'done_check'" % (task_routing.KIND_TASK.get("done-check"),))
    check(task_routing.TASK_KIND.get("done_check") == "done-check",
          "TASK_KIND['done_check'] is %r, expected 'done-check' (derived from KIND_TASK by inversion)"
          % (task_routing.TASK_KIND.get("done_check"),))


def test_model_roles_can_be_given_it():
    # THE FLOW TAB DECIDES WHETHER IT IS OFFERED (Jas, 2026-10-19). done_check is runnable by the
    # harness (KIND_TASK) but the Flow tab still names Claude as its actor and no model job runs it
    # yet, so it is offered to a role exactly when the Flow tab says a model does it.
    import json
    flow = {x["id"]: x for x in json.loads(task_routing.FLOW.read_text())["tasks"]}
    by_model = flow.get("done_check", {}).get("worker_kind") == "role"
    check("done_check" in task_routing.RUNNABLE_TASKS,
          "'done_check' is not runnable by the harness (KIND_TASK lost it)")
    check(("done_check" in task_routing.MODEL_TASKS) == by_model,
          "done_check is offered to roles exactly when the Flow tab names a model as its actor")


def test_the_store_decides_who_runs_it():
    check(task_routing.owner(STORE, "done-check") == "senior",
          "owner() does not resolve the kind to the role whose list has the task")
    check(bool(task_routing.entry(STORE, "senior", "done-check")),
          "entry() found no done_check entry on the role that lists it")
    check(task_routing.position(STORE, "senior", "done-check") == 0,
          "position() does not find the task on the role's list")
    check(task_routing.role_name("done_check", STORE) == "Senior Swift",
          "role_name('done_check') is %r, expected the store's display name"
          % (task_routing.role_name("done_check", STORE),))
    check(task_routing.role_name("done-check", STORE) == "Senior Swift",
          "role_name() must resolve a dispatcher kind through KIND_TASK too")
    check(task_routing.role_name("done_check", EMPTY) == "no role has done_check",
          "role_name() with no owner must say so, never fall back to a default role (D-39)")
    check(task_routing.render("{role:done_check} is checking", STORE).startswith("Senior Swift"),
          "render() did not substitute the store's name for the {role:done_check} token")


def test_the_new_kind_ranks_after_the_builtin_ones():
    lv = task_routing.levels(STORE)
    check("done-check" in lv, "levels() has no rank for the new kind")
    check(lv["done-check"] == task_routing.UNRANKED,
          "a store-added kind takes UNRANKED (%s), not an invented rank: %r"
          % (task_routing.UNRANKED, lv.get("done-check")))
    items = [{"kind": "supervision", "role": "manager", "created": "2026-10-01T00:00:00"},
             {"kind": "done-check", "role": "senior", "created": "2026-10-01T00:00:00"}]
    ordered = task_routing.order(STORE, items)
    check(ordered[0]["kind"] == "supervision",
          "order() put %s ahead of supervision at equal age; a cost-of-delay UNRANKED kind must not"
          " outrank built-in work" % (ordered[0]["kind"],))
    check(ordered[1]["kind"] == "done-check", "order() lost the done-check item")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                FAILS.append("%s raised %s: %s" % (name, type(e).__name__, e))
    for f in FAILS:
        print("FAIL:", f)
    print("test_done_check_routing: %d assertion(s) failed" % len(FAILS))
    sys.exit(1 if FAILS else 0)
