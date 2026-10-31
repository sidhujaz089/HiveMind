#!/usr/bin/env python3
"""No work KIND may share a term with a ROLE. Jas, 2026-10-28.

"make sure there is no overlap in role names so future sessions aren't confused. 'kind manager -> task
scoping. Those 14 "manager" literals are a work kind, not a role name' for example should not have manager
in the final build. Find new terms if any code shares role type of terms."

WHY IT MATTERED: 'manager' was a ROLE key, a TASK id ('scoping' belonged to the Manager role) and a WORK
KIND, all in one string. A future session reading `owner(ROUTING, "manager")` cannot tell whether it asks
for a role, a task or a kind -- and after a rename it silently returned None, because owner() maps a kind
through KIND_TASK and falls back to treating the string as a task name.

THE RULE: every kind in task_routing.KIND_TASK must be a TERM OF ITS OWN -- never a role key in
ROLES.json, and never a role's display name.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
ROLES = ROOT / "projects" / "local-inference" / "models" / "ROLES.json"


def main():
    import task_routing as T
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    store = json.loads(ROLES.read_text(encoding="utf-8"))
    roles = set(store.get("roles") or {})
    names = {str(r.get("name") or "").lower() for r in (store.get("roles") or {}).values()}
    names.discard("")
    kinds = set(T.KIND_TASK)
    clash = sorted(kinds & roles)
    check("no work kind is also a role key", not clash, f"kind(s) that are role keys: {clash}")
    clash2 = sorted(kinds & names)
    check("  and none is a role's display name", not clash2, f"kind(s) that are role names: {clash2}")
    check("  'manager' is no longer a kind -- it was the overlap Jas named", "manager" not in kinds,
          str(sorted(kinds)))
    check("  the scoping queue is its own term", T.KIND_TASK.get("scoping") == "scoping",
          str(T.KIND_TASK.get("scoping")))
    # A role may be deleted or added and these still hold: the invariant is about terms, not roles.
    fake = {"roles": {"worker": {"name": "Worker", "kind": "model", "tasks": []}}}
    check("  and the kind map does not name a role at all",
          not (set(T.KIND_TASK) & set(fake["roles"])), "")
    print("test_work_kinds_are_not_role_names:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
