#!/usr/bin/env python3
"""The Roles tab's task list comes from the Flow tab (Jas, 2026-10-19).

A role may be given a task only when the Flow tab names a model as its actor (worker_kind "role") AND
the harness can run it. This holds the two in step: a Flow model task with nothing to run it, or a
harness task the Flow tab gives to a script or a person, is named here."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import task_routing as t  # noqa: E402

# KNOWN MISMATCH, for Jas to decide: the Flow tab says the Manager triages new error shapes, but no job
# dispatches it (2026-10-19 search found none). Either build it or change its actor on the Flow tab.
KNOWN_UNRUN = set()      # triage was deleted from the Flow tab (change 76, 2026-10-19)

failed = 0


def check(name, ok, detail=""):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not ok else ""))


flow = {x["id"]: x for x in json.loads(t.FLOW.read_text())["tasks"]}
role_tasks = {k for k, x in flow.items() if x.get("worker_kind") == "role"}
check("every offered task is a model task on the Flow tab", t.MODEL_TASKS <= role_tasks,
      sorted(t.MODEL_TASKS - role_tasks))
check("every offered task can be run by the harness", t.MODEL_TASKS <= t.RUNNABLE_TASKS,
      sorted(t.MODEL_TASKS - t.RUNNABLE_TASKS))
unrun = role_tasks - t.RUNNABLE_TASKS
check("the only Flow model task nothing runs is the known one", unrun == KNOWN_UNRUN, sorted(unrun))
check("a task the Flow tab gives a person or a script is never offered",
      not any(flow[k].get("worker_kind") != "role" for k in t.MODEL_TASKS if k in flow))
check("done_check (Claude's on the Flow tab) is not offered", "done_check" not in t.MODEL_TASKS)
roles = t.load()
held = {(k, x["task"]) for k in t.model_roles(roles) for x in t.tasks_of(roles, k)}
check("no role holds a task off the list", all(task in t.MODEL_TASKS for _, task in held),
      sorted(h for h in held if h[1] not in t.MODEL_TASKS))
print("FAILED" if failed else "ok")
sys.exit(1 if failed else 0)
