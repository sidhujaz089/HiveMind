#!/usr/bin/env python3
"""Work BELOW Scoping on the Manager's list waits while the manager queue has a task (2026-09-30).

The manager queue already stood down for tasks ABOVE Scoping (the YIELD flag); nothing did the
reverse. With Scoping moved above RCA and Session review on the Models tab, a freed Q4 went to the
next session review -- which then waits up to 6 h for the lease -- and scoping lost every race.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import dispatch_loop as dl  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


def T(task):
    return {"task": task, "interrupt": False, "teamwork": False}


store = {"roles": {
    "manager": {"kind": "model", "tasks": [T("supervision"), T("scoping"), T("rca"), T("session_review")]},
    "senior": {"kind": "model", "tasks": [T("preflight"), T("session_review")]}}}
items = [{"kind": "session-review", "role": "manager", "key": "sr"}, {"kind": "rca", "role": "manager", "key": "rca"},
         {"kind": "supervise", "role": "manager", "key": "sup"}, {"kind": "session-review", "role": "senior", "key": "s2"}]
keep, held = dl.hold_below_scoping(items, store, waiting=True)
report("the Manager's tasks below Scoping wait while scoping has work", sorted(i["key"] for i in held), ["rca", "sr"])
report("  above Scoping (supervision) and another role's work are untouched", sorted(i["key"] for i in keep),
       ["s2", "sup"])
report("  nothing is held when the queue has nothing", dl.hold_below_scoping(items, store, waiting=False)[1], [])
store2 = {"roles": {"manager": {"kind": "model", "tasks": [T("supervision"), T("rca"), T("session_review"),
                                                           T("scoping")]}}}
report("Jas's order (RCA and Session review ABOVE Scoping): nothing held",
       dl.hold_below_scoping(items, store2, waiting=True)[1], [])

with tempfile.TemporaryDirectory() as tmp:
    q = Path(tmp)
    report("no progress log: not alive, nothing waits", dl.scoping_waiting(q, upcoming=lambda n: (["t1"], [])), False)
    (q / "progress.log").write_text("x\n")
    report("alive with a task: scoping waits", dl.scoping_waiting(q, upcoming=lambda n: (["t1"], [])), True)
    report("alive with nothing to scope: no", dl.scoping_waiting(q, upcoming=lambda n: ([], [])), False)
    old = time.time() - dl.MANAGER_QUEUE_ALIVE_S - 60
    os.utime(q / "progress.log", (old, old))
    report("a queue silent past the limit does not hold work", dl.scoping_waiting(q, upcoming=lambda n: (["t1"], [])),
           False)
    (q / "progress.log").write_text("x\n")
    (q / "STOP").write_text("")
    report("a STOPped queue does not hold work", dl.scoping_waiting(q, upcoming=lambda n: (["t1"], [])), False)
# SPARE SLOTS ARE FILLED (2026-10-02, -np 4): one slot stays for Scoping, the rest take held work.
q4 = [{"kind": "rca", "role": "manager", "key": f"r{n}", "model": "q4"} for n in range(5)]
four = lambda m: 4
report("4 slots, nothing running: 3 go, 2 wait (one slot kept for Scoping)",
       len(dl.hold_below_scoping(q4, store, waiting=True, slots=four)[1]), 2)
report("4 slots, 2 jobs on the model: 1 goes",
       len(dl.hold_below_scoping(q4, store, waiting=True, slots=four,
                                 running=[{"model": "q4"}, {"model": "q4"}])[1]), 4)
report("jobs on ANOTHER server running the same model do not fill this one",
       len(dl.hold_below_scoping(q4, store, waiting=True, slots=four,
                                 running=[{"model": "q4", "server_port": 11435}] * 3)[1]), 2)
report("1 slot: everything still waits, as before",
       len(dl.hold_below_scoping(q4, store, waiting=True, slots=lambda m: 1)[1]), 5)
# SCOPING ON THE SENIOR'S STATION (2026-10-03): scoping_queue.sh runs on station 2 when the Senior's
# list has Scoping above Preflight. Holding the main-server Manager's RCAs then held 30 for nothing.
st3 = {"roles": {
    "manager": {"kind": "model", "tasks": [T("supervision"), T("scoping"), T("rca")]},
    "senior": {"kind": "model", "endpoint": "http://127.0.0.1:11435",
               "tasks": [T("scoping"), T("preflight"), T("rca")]}}}
report("queue on the Senior's station: the main Manager's RCAs are not held",
       dl.hold_below_scoping(q4, st3, waiting=True, slots=lambda m: 1)[1], [])
st3["roles"]["senior"]["tasks"] = [T("preflight"), T("scoping")]
report("  queue back on the main server: held, as before",
       len(dl.hold_below_scoping(q4, st3, waiting=True, slots=lambda m: 1)[1]), 5)
# A ROLE WITHOUT BUILDING STILL RUNS ITS OWN WORK (2026-10-03): Building off the Manager made Q4
# "not an available model" for its RCAs. Priority work gets every main-server role's model.
report("main_role_models: main-server roles' models, station roles excluded",
       dl.main_role_models(st3 | {"roles": {**st3["roles"], "manager": {**st3["roles"]["manager"], "model": "q4"},
                                            "senior": {**st3["roles"]["senior"], "model": "q4s"}}}), ["q4"])
# NO BUILDING ROLE MEANS NO BUILDS (2026-10-03): an empty list fell back to gemma4:26b + bf16.
import role_store as _rs
_real = _rs.load
try:
    _rs.load = lambda path=None: {"roles": {"coder": {"kind": "model", "model": "g", "tasks": [T("rca")]}}}
    report("no role has Building: no production models (not the built-in fallback)", dl._production_models(), [])
    _rs.load = lambda path=None: {"roles": {"coder": {"kind": "model", "model": "g", "tasks": [T("building")]}}}
    report("  a role with Building: its model", dl._production_models(), ["g"])
finally:
    _rs.load = _real
src = (ROOT / "scripts" / "dispatch_loop.py").read_text()
report("the tick hands priority work the main roles' models", "schedule(priority_models, items" in src, True)
# UPDATED 2026-10-08: parallel scoping retired the hold in the tick -- Scoping now takes its turn in
# the lane rotation (scoping_items; test_parallel_scoping.py). hold_below_scoping stays tested above
# as a function, but the tick must no longer apply it.
report("the tick puts Scoping in the rotation instead of holding for it",
       ("scope_items, scope_running = scoping_items(running, model_slots)" in src,
        "items, behind_scoping = hold_below_scoping(items" in src), (True, False))
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
