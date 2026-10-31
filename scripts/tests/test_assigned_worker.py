#!/usr/bin/env python3
"""Assigned items (plan 3b): a task Assigned to: a MODEL role is worked by that role, not parked.

Jas, 2026-09-29: "a model role: the item goes into that role's Assigned items queue ... The role
works it with the note and the dossier as input." Before assigned_worker.py a task assigned to the
Manager sat forever: manager_next skips every assigned task and nothing else looked. A temporary
task store; the model is a stub, so nothing is asked of a real one.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import assigned_worker as aw  # noqa: E402
import role_store  # noqa: E402
import taskstore  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


def main():
    # A FIXTURE ROLE STORE, NOT THE LIVE ONE (t1498). This suite read role_store.load(), so the
    # org chart Jas reorders on the Roles tab IS the test's input: on 2026-10-19 the chart became
    # jas, claude, manager, auditor, coder, embedder, dsh -- "claude" stopped being adjacent to
    # "jas" (the roles assertion went red) and the suite's own role "senior" is in no store at
    # all, so prompt_for's d["roles"]["senior"] raised KeyError. Same rule as t657 below: a test
    # pins the state it reasons about. Key order IS rank order (role_store.rank = list(d["roles"])),
    # so claude and jas stay adjacent, and senior sits below position 2 so the real escalate()
    # resolution ("the nearest role above that is not an agent") is exercised, not assumed.
    d = {"roles": {"manager": {"name": "Manager", "model": "swift", "kind": "model", "lanes": 4, "escalations": ["rework"],
                               "tools": {"read_file": True, "run_command": True}},
                   "claude": {"name": "Claude", "kind": "person", "escalations": ["technical"]},
                   "jas": {"name": "Jas", "kind": "person", "escalations": ["vision"]},
                   "senior": {"name": "Senior", "model": "qwen3.8:27b-mtp-bf16", "kind": "model"},
                   "embedder": {"name": "Embedder", "model": "qwen3-embedding:0.6b", "kind": "model",
                                "lanes": 0}},
         "_escalation": {"vision": 1, "technical": 2, "rework": 3}}
    # A role that returns two unusable answers is escalated by assigned_worker.run with NO store
    # (assigned_worker.py: role_store.escalate("technical", role)), so the real function loads the
    # LIVE ROLES.json and raises NoEscalation: 'senior' is not a role in it. Resolve the class
    # against the fixture d -- the rule is the real one, the chart is the test's. A stub, like the
    # ask=stub(...) below; the third parameter is d in role_store.escalate(cls, from_role, d).
    _real_escalate, role_store.escalate = role_store.escalate, (
        lambda cls, from_role=None, dd=None: _real_escalate(cls, from_role, d))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        import skills
        skills.LOADS = tmp / "skill-loads.jsonl"     # never the live log (it did, until 2026-09-30)
        tasks = tmp / "tasks"
        tasks.mkdir()
        taskstore.TASKS, taskstore.DONE = tasks, tasks / "done"
        taskstore.QUEUE, taskstore.LOCK = tasks / "QUEUE.md", tasks / ".lock"
        aw.STATE = tmp / "state"
        a = taskstore.add("Why does the tick log say nothing at 03:00?", area="dispatch", body="The log is empty.")
        b = taskstore.add("Pick a provider", area="web")
        c = taskstore.add("Unassigned task", area="x")
        taskstore.set_meta(a, needs="manager")
        taskstore.add_note(a, "Manager: check dispatch-loop-cron.log around 03:00 and say what ran.", who="jas", _from_form=True)
        taskstore.set_meta(b, needs="jas")
        taskstore.set_meta(c, needs="")
        report("pending: only tasks assigned to a MODEL role, with that role", aw.pending(d), [(a, "manager")])

        p = aw.prompt_for(a, "manager", d)
        report("the prompt carries the task and its newest note",
               ("Why does the tick log say nothing" in p, "check dispatch-loop-cron.log" in p), (True, True))
        # The roles a model may name in OUTCOME: assign, computed from the fixture -- not a
        # hardcoded adjacency. The literal "claude, jas" was a coincidence of the 2026-09 chart
        # and went red the day the chart was reordered (2026-10-19).
        report("  and the outcome lines, with the roles it may assign to",
               ("OUTCOME: done" in p, ", ".join(role_store.assign_order(d)) in p), (True, True))
        roles = set(role_store.assign_order(d))
        report("outcome: done", aw.outcome_of("did it\nOUTCOME: done", roles), ("done", ""))
        report("outcome: assign a known role", aw.outcome_of("x\nOUTCOME: assign claude", roles), ("assign", "claude"))
        report("outcome: an unknown role is not an outcome", aw.outcome_of("OUTCOME: assign nobody", roles), None)
        report("outcome: none", aw.outcome_of("I looked.", roles), None)

        asked = []

        def stub(text):
            def ask(prompt, model, role, tools):
                asked.append((model, role, tools))
                return 0, text
            return ask
        rc = aw.run(a, "manager", "m:1", ask=stub("Nothing ran at 03:00: the timer was stopped (log line 12).\n"
                                                  "OUTCOME: done"), d=d)
        report("done: the answer is noted, signed by the role", (rc, "Nothing ran at 03:00" in taskstore.find(a)[1],
                                                                 "Manager - " in taskstore.find(a)[1]), (0, True, True))
        report("  the OUTCOME line is not in the note", "OUTCOME:" in taskstore.find(a)[1], False)
        report("  and it is unassigned, back in the queue", (taskstore.find(a)[0]["needs"], taskstore.find(a)[0]["status"]),
               ("production", "queued"))   # 2026-10-03: released = Production
        report("  the Manager was asked with its tools", asked[-1], ("m:1", "manager", True))
        report("  and it is no longer pending", aw.pending(d), [])
        report("a task no longer assigned to the role is left alone", aw.run(a, "manager", "m:1", ask=stub("x"), d=d), 0)

        taskstore.set_meta(a, needs="senior")
        rc = aw.run(a, "senior", "m:2", ask=stub("Needs Jas's call on the timer.\nOUTCOME: assign jas"), d=d)
        # NEEDS IS A TIER (Claude, 2026-10-29): "assign jas" is stored as the class Jas holds.
        report("assign: goes to the named role's tier", (rc, taskstore.find(a)[0]["needs"]), (0, "vision"))
        report("  a non-Manager role is asked without tools", asked[-1][2], False)

        taskstore.set_meta(a, needs="senior")
        report("a held lease is a wait, nothing counted", aw.run(a, "senior", "m:2", ask=lambda *x: (4, ""), d=d), 4)
        r1 = aw.run(a, "senior", "m:2", ask=stub("no outcome line"), d=d)
        report("one unusable answer: still with the role", (r1, taskstore.find(a)[0]["needs"]), (1, "senior"))
        r2 = aw.run(a, "senior", "m:2", ask=stub("still none"), d=d)
        # THE DROP-DOWN'S ROLE (Jas, 2026-10-27: "All rework, technical, or vision escalations go to the
        # role in the drop down"). In this fixture's order (manager, claude, jas, senior) technical is
        # position 2 = claude. It was "jas" under the +1 rule that this replaced.
        report("two: escalated to the drop-down role, with where the answers are",
               (r2, taskstore.find(a)[0]["needs"], f"assigned-items/{a}.json" in taskstore.find(a)[1]), (1, "technical", True))
    # THE DISPATCHER queues one item per assignment, for THAT role, on ITS model.
    import dispatch_loop as dl
    real, real_routing = aw.pending, dl.ROUTING
    aw.pending = lambda d=None: [("t901", "manager"), ("t902", "embedder")]
    # A FIXTURE STORE, NOT THE LIVE ONE (t657): Jas took Assigned items off the Manager on the
    # Models tab on 2026-10-03 and this suite went red, though nothing in the code had changed.
    import copy
    dl.ROUTING = copy.deepcopy(real_routing)
    dl.ROUTING["roles"]["manager"]["tasks"] = [{"task": "assigned_items", "interrupt": False, "teamwork": True}]
    dl.ROUTING["roles"]["embedder"]["tasks"] = []
    try:
        items = {i["key"]: i for i in dl.priority_items([], [], {}, history=[], objections={}, preflight=False)
                 if i["kind"] == "assigned_items"}
    finally:
        aw.pending, dl.ROUTING = real, real_routing
    report("dispatcher: the Manager's assignment is an item for the Manager on its model",
           (items["t901"]["role"], items["t901"]["model"], items["t901"]["assigned_to"]),
           ("manager", dl.ROLES.get("manager"), "manager"))
    report("  a role without Assigned items on its list: role None, so it waits and the tick says why",
           items["t902"]["role"], None)
    q = dict((i["key"], why) for i, why in dl.schedule(set(dl.ROLES.values()), [items["t902"]], [])[2])
    report("  (schedule queues it: 'no role has this task on its list')", "no role" in q.get("t902", ""), True)
    # FALLBACK TO CLAUDE: work no role's list has is filed once for Claude, never left in silence.
    filed, metas, notes_ = [], {}, []
    fake_add = lambda title, **k: filed.append((title, k.get("needs"))) or "t999"
    out = dl.no_role_fallback([{"kind": "rca", "role": None, "key": "f1"}, {"kind": "rca", "role": None, "key": "f2"},
                               {"kind": "preflight", "role": "senior", "key": "p1"},
                               {"kind": "assigned_items", "role": None, "key": "t902", "assigned_to": "embedder"}],
                              add=fake_add, ordered=lambda: [],
                              set_meta=lambda t, **k: metas.update({t: k}), add_note=lambda t, n, who: notes_.append(t))
    report("no role does rca: ONE task for Claude, naming it", filed, [("No role does rca", "claude")])
    report("  an assignment to a role without Assigned items goes to Claude", metas, {"t902": {"needs": "claude"}})
    report("  work a role does is left alone", any("p1" in x for x in out), False)
    filed.clear()
    dl.no_role_fallback([{"kind": "rca", "role": None, "key": "f1"}], add=fake_add,
                        ordered=lambda: [({"title": "No role does rca", "status": "queued"}, "")])
    report("  not filed again while that task is open", filed, [])
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
