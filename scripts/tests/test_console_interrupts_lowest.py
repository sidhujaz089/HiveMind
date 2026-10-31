#!/usr/bin/env python3
"""GATE: a Console message takes a FREE lane, and with every lane full and Interrupt on it stops the
job LOWEST in its role's priority list (Jas, 2026-10-12). Hermetic: fits() is stubbed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import dispatch_loop as d
import task_routing as t

d.fits = lambda m, busy: True
# HERMETIC ROLE LIST (2026-10-14): this read the live store, so Jas removing `building` from the
# Manager on the Models tab turned it red and held every build on the line ("red on main"). The
# rule under test is "stop the lowest on the role's list", not "what the list is today".
import copy
d.ROUTING = copy.deepcopy(d.ROUTING)
d.ROUTING["roles"]["manager"]["tasks"] = [
    {"task": k, "interrupt": False, "teamwork": False}
    for k in ("console", "supervision", "assigned_items", "preflight", "scoping", "script_audit", "building")]
order = [x["task"] for x in t.tasks_of(d.ROUTING, "manager")]
run = [{"model": "swift", "kind": "production", "pid": "11", "elapsed_s": 100},
       {"model": "swift", "kind": "preflight", "pid": "12", "elapsed_s": 60},
       {"model": "swift", "kind": "scoping", "pid": "13", "elapsed_s": 60}]   # scoping: last on the manager list (script-review moved to the Auditor, 2026-10-12)
msgs = [{"id": "m1", "tid": "t"}]
res = []
pre = []
st, _ = d.assign_console(msgs, ["swift"], run, [], owners=["swift"], slots=lambda m: 4, interrupt=True, preempt=pre)
res.append(("a free lane is taken without interrupting", (len(st), pre), (1, [])))
run.append({"model": "swift", "kind": "production", "pid": "14", "elapsed_s": 50})
pre = []
st, _ = d.assign_console(msgs, ["swift"], run, [], owners=["swift"], slots=lambda m: 4, interrupt=True,
                         can_checkpoint=True, preempt=pre)
# Lowest on the list loses; between equals (11 and 14 are both builds) the one with the least work
# done (14, 50 s) is stopped -- the cheapest to lose. Named, not recomputed with the code's own key.
low = "14"
res.append(("lanes full + Interrupt on: the LOWEST-priority job is stopped", pre, [low]))
res.append(("  and the message starts in its lane", len(st), 1))
pre = []
st, _ = d.assign_console(msgs, ["swift"], run, [], owners=["swift"], slots=lambda m: 4, interrupt=False, preempt=pre)
res.append(("lanes full + Interrupt off: it waits, nothing stopped", (len(st), pre), (0, [])))
bad = 0
for name, got, want in res:
    ok = got == want
    bad += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f": got {got!r}, want {want!r}"))
print(f"  (manager list: {order})\n\n{len(res) - bad} passed, {bad} failed")
sys.exit(1 if bad else 0)
