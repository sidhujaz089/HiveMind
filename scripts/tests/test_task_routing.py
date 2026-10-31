#!/usr/bin/env python3
"""The dispatcher routes by the role store's task lists (plan step 4): the rules in
scripts/task_routing.py, and dispatch_loop following them when a list changes. Fakes only: no
Ollama, no ledger, no launched process.

THE BEFORE/AFTER PROOF this was built against (2026-09-30): the old dispatcher (607e29af) and this
one were given the same 3,645 fixed situations -- every mix of 0-2 supervisions, preflights, script
reviews, session reviews and RCAs, senior station up and down, the Q4 model idle / building /
scoping. They agreed on all of them once the Senior's session_review and rca Interrupt boxes were
on; with the plan's table (off) the only differences were those 8 situations where the station is
down and a Q4 build would have been stopped for a session review. That comparison needs the old
file, so it is recorded here, not re-run.
"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import task_routing as tr  # noqa: E402
import dispatch_loop as dl  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def T(task, interrupt=False, teamwork=False):
    return {"task": task, "interrupt": interrupt, "teamwork": teamwork}


# THIS SUITE'S OWN STORE (t1101 chunk 1, 2026-10-13). It used task_routing.DEFAULT, the built-in copy
# the router fell back to when the role store could not be read; t1101 removes that fallback (Jas: "I
# dont want any task to have a fallback"), so the fixture lives here. Same content DEFAULT had.
STORE = {"roles": {
    "manager": {"kind": "model", "tasks": [{"task": "supervision", "interrupt": True, "teamwork": False},
                                           {"task": "triage", "interrupt": False, "teamwork": False},
                                           {"task": "scoping", "interrupt": False, "teamwork": False},
                                           {"task": "console", "interrupt": False, "teamwork": False}]},
    "auditor": {"kind": "model", "tasks": [{"task": "script_review", "interrupt": True, "teamwork": False}]},
    "coder": {"kind": "model", "tasks": [{"task": "building", "interrupt": False, "teamwork": False}]},
    "embedder": {"kind": "model", "tasks": [{"task": "corpus_index", "interrupt": False, "teamwork": False}]},
    "senior": {"kind": "model", "endpoint": "http://127.0.0.1:11435",
               "tasks": [{"task": "preflight", "interrupt": True, "teamwork": False},
                         {"task": "session_review", "interrupt": False, "teamwork": False},
                         {"task": "rca", "interrupt": False, "teamwork": False}]},
}}


def main():
    D = copy.deepcopy(STORE)
    # The manager queue is live state; every case here pins it off (2026-10-02: a live queue with work
    # made the Senior rank Scoping and this suite failed on the box's state, not on code).
    dl.scoping_on_station = lambda h, mq=None: {"ours": False, "running": None, "pending": False,
                                                "streak": 0, "rankable": False}
    # ---- the rules
    lst = [T("a"), T("b"), T("c")]
    report("a free role takes the first task on its list with work", tr.next_task(lst, {"b": 1, "c": 2}, set()), "b")
    report("no work anywhere -> nothing", tr.next_task(lst, {}, set()), None)
    # NO TEAM WORK (Jas, 2026-10-14): another role being on a task never keeps this one off it.
    report("another role is on b: it takes b anyway -- b is first on its list with work",
           tr.next_task(lst, {"b": 1, "c": 1}, {"b"}), "b")
    report("another role is on c and only c waits: it takes c", tr.next_task(lst, {"c": 1}, {"c"}), "c")
    L = [T("hi", interrupt=True), T("mid"), T("lo")]
    report("Interrupt on + higher + checkpointable -> pause the running job", tr.may_interrupt(L, "hi", "lo", True), True)
    report("a model-call review is never paused (not checkpointable)", tr.may_interrupt(L, "hi", "lo", False), False)
    report("Interrupt off -> finish first, then switch", tr.may_interrupt(L, "mid", "lo", True), False)
    report("never interrupts a HIGHER task", tr.may_interrupt([T("lo"), T("hi", interrupt=True)], "hi", "lo", True), False)
    report("the manager queue yields for the tasks above Scoping on the Manager's list",
           tr.yield_kinds(D), {"supervise", "triage"})
    report("  moving Scoping to the top: it yields for nothing",
           tr.yield_kinds({"roles": {"manager": {"tasks": [T("scoping"), T("supervision")]}}}), set())
    lv = tr.levels(D)
    report("with today's lists, LEVELS is the cost-of-delay order it replaced",
           {k: lv[k] for k in tr.COST_OF_DELAY}, tr.COST_OF_DELAY)
    report("owners come from the lists", (tr.owner(D, "supervise"), tr.owner(D, "script-review"),
                                          tr.owner(D, "rca"), tr.owner(D, "production")),
           ("manager", "auditor", "senior", "coder"))
    report("a task nobody has -> no owner", tr.owner(D, "web-research"), None)

    # a role's own list re-orders ITS items inside the slots the cost-of-delay order gave them
    items = [{"kind": "session-review", "role": "senior", "key": "s"}, {"kind": "rca", "role": "senior", "key": "r"},
             {"kind": "supervise", "role": "manager", "key": "f"}, {"kind": "preflight", "role": "senior", "key": "p"}]
    report("today's lists: the old order", [i["key"] for i in tr.order(D, items)], ["p", "f", "s", "r"])
    D2 = copy.deepcopy(D)
    D2["roles"]["senior"]["tasks"] = [T("rca"), T("preflight"), T("session_review")]
    report("Senior puts RCA first: its RCA takes its first slot; the Manager's work keeps its place",
           [i["key"] for i in tr.order(D2, items)], ["r", "f", "p", "s"])

    # ---- the dispatcher follows the store
    real = (dl.ROUTING, dl.ROLES, dl.classify, dl.ledger_running, dl.launch)
    import supervise
    import truncation_review
    import session_review
    import script_audit
    real_q = (supervise.pending, supervise.key, truncation_review.pending_requests,
              session_review.pending_requests, session_review.pending_findings)
    real_sa = script_audit.pending_requests
    script_audit.pending_requests = lambda: []      # t790: a real queued audit must not leak in
    import assigned_worker
    real_aw = assigned_worker.pending
    assigned_worker.pending = lambda d=None: []     # 2026-10-14: a real task Assigned to the Manager (t738) leaked in
    Q, G = "q4", "gemma"
    try:
        dl.ROLES = {"manager": Q, "auditor": Q, "senior": Q, "coder": G}
        dl.classify = lambda ch, running, red_of, history=None, objections=None: (list(ch), [], [])
        dl.ledger_running = lambda history=None: []
        dl.launch = lambda job: (1, dl.REPO / "x.log")
        supervise.pending = lambda h, l: []
        supervise.key = lambda f: f["chunk"]
        truncation_review.pending_requests = lambda: [{"at": "S1", "path": "a.py", "sha256": "x"}]
        session_review.pending_requests = lambda: [Path("/x/sess.json")]
        session_review.pending_findings = lambda: []
        build = [{"model": Q, "kind": "production", "pid": 7, "elapsed_s": 60}]

        dl.ROUTING = copy.deepcopy(D)
        items = [i for i in dl.priority_items([], [], None, [], [], preflight=False)
                 if i.get("role") not in dl.station_roles()]
        report("senior up: the main server takes the auditor's script review, not the session review",
               [(i["kind"], i["role"]) for i in items], [("script-review", "auditor")])
        s, pre, q, res = dl.schedule([Q, G], items, build, can_checkpoint=True)
        report("Auditor's Interrupt box on: its script review stops a checkpointable Q4 build", pre, [7])
        dl.ROUTING["roles"]["auditor"]["tasks"][0]["interrupt"] = False
        items = [i for i in dl.priority_items([], [], None, [], [], preflight=False)
                 if i.get("role") not in dl.station_roles()]
        s, pre, q, res = dl.schedule([Q, G], items, build, can_checkpoint=True)
        report("box off: the build is left to finish", pre, [])
        report("  and the queue says why", "Interrupt is off" in q[0][1], True)
        report("  and no new build starts on that model meanwhile", res, {Q})

        # Script review moved from the Auditor to the Senior on the Models tab
        dl.ROUTING = copy.deepcopy(D)
        dl.ROUTING["roles"]["auditor"]["tasks"] = []
        dl.ROUTING["roles"]["senior"]["tasks"].insert(1, T("script_review"))
        items = [i for i in dl.priority_items([], [], None, [], [], preflight=False)
                 if i.get("role") not in dl.station_roles()]
        report("script review moved to the Senior: the main server no longer takes it", items, [])
        senior = {"tag": Q, "endpoint": "http://127.0.0.1:11435", "port": 11435, "lease": "l"}
        job = dl.senior_slot(senior, [], None, [], dry_run=True)
        report("  the idle senior takes it, in its list's order (before the session review)",
               (job["kind"], job["key"]), ("script-review", "a.py"))
        job = dl.senior_slot(senior, [{"chunk": "p1", "brief": "b", "brief_sha256": "h"}], None, [], dry_run=True)
        report("  a preflight still comes first -- it is first on the list", (job["kind"], job["key"]), ("preflight", "p1"))

        # No Team Work: the manager also gets Session review
        dl.ROUTING = copy.deepcopy(D)
        dl.ROUTING["roles"]["manager"]["tasks"].insert(1, T("session_review"))
        dl.ledger_running = lambda history=None: [{"kind": "session-review", "key": "other", "model": Q,
                                                   "pid": 3, "server_port": 11434}]
        truncation_review.pending_requests = lambda: []
        job = dl.senior_slot(senior, [], None, [], dry_run=True)
        report("the manager is on session reviews, and the idle senior takes one too (no Team Work, 2026-10-14)",
               (job or {}).get("kind"), "session-review")
        dl.ROUTING["roles"]["senior"]["tasks"][1]["teamwork"] = True
        job = dl.senior_slot(senior, [], None, [], dry_run=True)
        report("Team Work on for the senior: it joins", job and job["kind"], "session-review")

        # nobody does it
        dl.ROUTING = copy.deepcopy(D)
        dl.ROUTING["roles"]["auditor"]["tasks"] = []
        dl.ledger_running = lambda history=None: []
        truncation_review.pending_requests = lambda: [{"at": "S1", "path": "a.py", "sha256": "x"}]
        items = [i for i in dl.priority_items([], [], None, [], [], preflight=False) if i["kind"] == "script-review"]
        s, pre, q, res = dl.schedule([Q, G], items, [], can_checkpoint=True)
        report("no role has Script review: nothing starts, and it says why",
               (s, q[0][1]), ([], "no role has this task on its list (Models tab)"))
        report("preflight moved to the Manager: the main server takes preflights although the station is up",
               (lambda d: (dl.ROUTING.__setitem__("roles", d["roles"]), dl.main_preflights(senior))[1])(
                   {"roles": {**copy.deepcopy(D)["roles"],
                              "manager": {"kind": "model", "tasks": [T("preflight"), T("scoping")]},
                              "senior": {"kind": "model", "endpoint": "http://127.0.0.1:11435",
                                         "tasks": [T("session_review")]}}}), True)
    finally:
        dl.ROUTING, dl.ROLES, dl.classify, dl.ledger_running, dl.launch = real
        (supervise.pending, supervise.key, truncation_review.pending_requests,
         session_review.pending_requests, session_review.pending_findings) = real_q
        script_audit.pending_requests = real_sa
        assigned_worker.pending = real_aw
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
