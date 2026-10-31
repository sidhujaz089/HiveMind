#!/usr/bin/env python3
"""Every process asks the store who does a job (t9554 S1). Rename the role, swap its model, move a
responsibility: the four lookups follow with no code edit, and a miss is UNKNOWN -- never the input echoed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import task_routing as T  # noqa: E402

FAILED = 0


def check(name, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def store(key="line", model="m-one", tasks=("preflight", "building"), tools=None, esc=("rework",)):
    return {"roles": {"boss": {"kind": "person", "escalations": ["vision"]},
                      "fixer": {"kind": "person", "escalations": ["technical"]},
                      key: {"kind": "model", "model": model, "escalations": list(esc),
                            "tools": tools or {"read_file": True, "write_file": False},
                            "tasks": [{"task": t} for t in tasks]}}}


d = store()
check("who_does names the role holding the responsibility", T.who_does("preflight", d), "line")
check("model_for is that role's model", T.model_for("building", d), "m-one")
d2 = store(key="crew")
check("a RENAMED role is found with no code edit", T.who_does("preflight", d2), "crew")
d3 = store(model="m-two")
check("a SWAPPED model is used with no code edit", T.model_for("preflight", d3), "m-two")
d4 = store(tasks=("building",))
check("a MOVED responsibility: nobody does preflight -> UNKNOWN", T.who_does("preflight", d4), T.UNKNOWN)
check("  and its model is UNKNOWN, not a guess", T.model_for("preflight", d4), T.UNKNOWN)
check("a mistyped job is UNKNOWN, never echoed back as a role", T.who_does("prefligth", d), T.UNKNOWN)
check("holders follow the escalations (technical)", T.holders("technical", d), ["fixer"])
check("holders follow the escalations (rework)", T.holders("rework", d), ["line"])
check("tools come from the role's own entry", T.tools_for("line", d), {"read_file": True, "write_file": False})
check("tools for a role not on the store: UNKNOWN", T.tools_for("ghost", d), T.UNKNOWN)
print("test_role_lookups_follow_the_store:", "ok" if not FAILED else "FAILED")
sys.exit(1 if FAILED else 0)
