#!/usr/bin/env python3
"""Every suite allowed to fail names an OPEN task that owns making it pass (t660).

From the Senior's RCA workspace/rca/6a7db0132baf.md: a red suite can sit declared in
test-failures-ceiling.json with nobody's name on it, invisible to enforcement and never cleared.
Each allowed_to_fail entry must have a reason (in any of the ceiling's reason maps) that starts with
a task id, and that task must be open -- not done, not cancelled, and existing. Reads the repo's own
ceiling and task store, like test_every_chunk_has_task.py; writes nothing.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASKS = ROOT / "workspace" / "tasks"


def unowned(ceiling, open_ids):
    reasons = {}
    for v in ceiling.values():
        if isinstance(v, dict):
            reasons.update(v)
    bad = []
    for suite in ceiling.get("allowed_to_fail") or []:
        m = re.match(r"\s*(t\d+)\b", str(reasons.get(suite, "")))
        if not m:
            bad.append(f"{suite}: its reason names no task")
        elif m.group(1) not in open_ids:
            bad.append(f"{suite}: owner {m.group(1)} is not an open task")
    return bad


def main():
    fails = []
    # the rule itself, on fixtures
    c = {"allowed_to_fail": ["a.py", "b.py", "c.py"], "_r": {"a.py": "t1 (x): why", "b.py": "no owner", "c.py": "t9: x"}}
    got = unowned(c, {"t1"})
    if got != ["b.py: its reason names no task", "c.py: owner t9 is not an open task"]:
        fails.append(f"the rule on fixtures: {got}")
    # the live ceiling
    ceiling = json.loads((ROOT / "workspace" / "test-failures-ceiling.json").read_text(encoding="utf-8"))
    open_ids = {f.name.split("--")[0] for f in TASKS.glob("t*.md")}
    for b in unowned(ceiling, open_ids):
        fails.append("UNOWNED " + b)
    for f in fails:
        print("FAIL:", f)
    print("test_ceiling_owned:", "FAILED" if fails else "ok -- every allowed failure has an open owner")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
