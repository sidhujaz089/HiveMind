#!/usr/bin/env python3
"""flow_lint.py: a Flow edit is applied only when it has no error, no NEW warning and no consequence.

Jas, 2026-09-29 (plan step 2a-ii): "an edit to the flow cannot break things." Fixture flows only;
the real FLOW.json is read, never written.
"""
import copy
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "scripts")]
import flow_lint as fl  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


def T(id, name, outcomes=("done",), ends=(), outputs=("x",), inputs=(), **k):
    return {"id": id, "name": name, "worker": "Manager", "worker_kind": "role", "command": f"{id}.py",
            "queue": "q", "inputs": list(inputs), "outputs": list(outputs), "outcomes": list(outcomes),
            "triggers": {}, "note": "", "ends": list(ends), "status": "active", **k}


def L(a, o, b, carry=("x",), **k):
    return {"from": a, "outcome": o, "to": b, "mover": f"{a}.py", "carry": list(carry), **k}


BASE = {"tasks": [T("a", "A"), T("b", "B", ends=("done",), inputs=("x",))],
        "links": [L("a", "done", "b")],
        "sources": [{"id": "s", "name": "S", "to": "a", "mover": "m", "status": "active"}]}
ROLES = {"manager": {"name": "Manager", "tasks": [{"task": "b"}]}}


def run(ops, base=None):
    return fl.lint(base or BASE, ops, roles=ROLES, layout=False)


def main():
    before = copy.deepcopy(BASE)
    r = run([{"op": "edit_task", "id": "a", "set": {"note": "clearer words"}}])
    report("describing a built-in task differently is clean", fl.clean(r), True)
    report("  and the before flow is never modified", BASE, before)

    r = run([{"op": "edit_link", "from": "a", "outcome": "done", "to": "b", "set": {"mover": "a.py (renamed)"}}])
    report("relabelling a link's mover is clean", fl.clean(r), True)

    r = run([{"op": "delete_task", "id": "b"}])
    report("deleting a task is never clean", fl.clean(r), False)
    report("  it names the roles that list the task",
           any("Manager" in c for c in r["consequences"]), True)
    report("  and the link that goes with it",
           any("removes the link a --done--> b" in c for c in r["consequences"]), True)
    report("  and the outcome it leaves pointing nowhere is a NEW warning",
           any("A: outcome 'done' goes nowhere" in w for w in r["warnings"]), True)

    r = run([{"op": "edit_task", "id": "a", "set": {"outcomes": ["done", "failed"]}}])
    report("changing what a built-in task does is a consequence",
           any("changes what the built-in task A does (outcomes" in c for c in r["consequences"]), True)
    report("  and its new unlinked outcome a warning",
           any("'failed' goes nowhere" in w for w in r["warnings"]), True)

    r = run([{"op": "insert_task", "from": "a", "outcome": "done", "to": "b",
              "task": {"id": "check", "name": "Check it", "outputs": ["x"], "inputs": ["x"]},
              "outcome_out": "ok"}])
    after = r["after"]
    report("insert_task splits A -> B into A -> new -> B",
           sorted((l["from"], l["outcome"], l["to"]) for l in after["links"]),
           [("a", "done", "check"), ("check", "ok", "b")])
    report("  and says plainly that no role has the new model task on its list yet",
           any("no role has it on its task list" in c for c in r["consequences"]), True)
    r2 = fl.lint(BASE, [{"op": "insert_task", "from": "a", "outcome": "done", "to": "b",
                         "task": {"id": "check", "name": "Check it", "outputs": ["x"], "inputs": ["x"]},
                         "outcome_out": "ok"}],
                 roles={"m": {"name": "Manager", "tasks": [{"task": "check"}]}}, layout=False)
    report("  a model task a role already lists is NOT that consequence (task_worker.py runs it)",
           any("no role has it" in c for c in r2["consequences"]), False)
    report("  and that the built-in's script still sends work to the old target",
           any("removes or re-routes the link a --done--> b" in c for c in r["consequences"]), True)

    model = copy.deepcopy(BASE)
    model["tasks"].append(T("m", "M", ends=("done",), inputs=(), model_task=True))
    model["links"].append(L("b", "done", "m"))
    model["tasks"][1]["ends"] = []
    r = run([{"op": "edit_task", "id": "m", "set": {"prompt": "do it better"}}], base=model)
    report("editing a model task's prompt is clean", (r["errors"], r["warnings"], r["consequences"]), ([], [], []))

    r = run([{"op": "edit_task", "id": "nope", "set": {"note": "x"}}])
    report("an edit naming a task that does not exist is an error, not a guess",
           r["errors"], ["op 1 (edit_task): no task 'nope'"])
    r = run([{"op": "frobnicate"}])
    report("an unknown op is an error", bool(r["errors"]) and r["after"] is None, True)
    r = run([])
    report("an empty edit is an error", bool(r["errors"]), True)
    r = run([{"op": "add_task", "task": {"id": "a", "name": "again"}}])
    report("adding a task with a taken id is an error", r["errors"], ["op 1 (add_task): task 'a' already exists"])

    r = run([{"op": "add_link", "link": {"from": "a", "outcome": "done", "to": "ghost", "carry": ["x"]}}])
    report("a link to a task that does not exist is a warning",
           any("does not exist (ghost)" in w for w in r["warnings"]), True)
    r = run([{"op": "edit_link", "from": "a", "outcome": "done", "to": "b", "set": {"carry": ["y"]}}])
    report("a link carrying what its task does not produce is a warning",
           any("carries 'y'" in w for w in r["warnings"]), True)

    orphan = copy.deepcopy(BASE)
    orphan["tasks"].append(T("z", "Z", ends=("done",)))           # already fed by nothing
    r = run([{"op": "edit_task", "id": "a", "set": {"note": "x"}}], base=orphan)
    report("only NEW problems count: an existing orphan does not block an unrelated edit", fl.clean(r), True)

    r = run([{"op": "add_task", "task": {"id": "n", "name": "New", "about": "what it does, in plain words"}}])
    report("add_task keeps the plain-English description (dropped until 2026-10-13)",
           next((t.get("about") for t in r["after"]["tasks"] if t["id"] == "n"), None), "what it does, in plain words")

    real = fl.load()
    tid = real["tasks"][0]["id"]
    r = fl.lint(real, [{"op": "edit_task", "id": tid, "set": {"note": real["tasks"][0].get("note", "") + " ."}}])
    report("on the real FLOW.json, a note edit is clean (layout included)", (r["errors"], r["warnings"], r["consequences"]),
           ([], [], []))
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
