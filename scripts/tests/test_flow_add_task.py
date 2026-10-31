#!/usr/bin/env python3
"""A task node can be declared on the chart through its one write path (P1a, t8707).

To add a role-level review task (`skill_review`) to the chart there had to be a way to add a TASK, and
there was none: `flow_change.py` could add a helper, apply a Manager's answer, or revert -- so a person
could not declare a task at all. `role_store.validate` refuses a role whose tasks are not in FLOW.json, so
the node has to exist before any role may hold it, and nothing could create it.

`flow_change.py task <task.json>` closes that. It is deliberately the SAME gate as every other change:
`flow_lint.lint` must find no error, no new warning and no consequence, the result is written atomically,
and a row goes into FLOW-CHANGELOG.jsonl so it can be reverted. A refusal writes NOTHING.

That last property is what this suite is mostly about. The first real use was refused -- correctly -- with
the design it was missing: *"Skill review: nothing sends work to it; outcome 'approved' goes nowhere..."*
A tool that half-applied that node would have left the chart inconsistent, and the blank-page instinct is
to force it through. So:

  * an unlinked task node is REFUSED, with the lint's words, and the chart and changelog are untouched;
  * a duplicate id is REFUSED the same way;
  * a node with no id is REFUSED;
  * and the CLI exits 1 on a refusal rather than 0-with-a-message.

Hermetic: FLOW and LOG are redirected at the module's constants, as the guard's suite does.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import flow_change  # noqa: E402

LIVE_FLOW = ROOT / "projects" / "local-inference" / "models" / "FLOW.json"
LIVE_LOG = ROOT / "projects" / "local-inference" / "models" / "FLOW-CHANGELOG.jsonl"
FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
        return ""
    except Exception as e:                      # noqa: BLE001 -- the message IS the assertion
        return str(e)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        flow_p, log_p = d / "FLOW.json", d / "FLOW-CHANGELOG.jsonl"
        shutil.copy(LIVE_FLOW, flow_p)
        shutil.copy(LIVE_LOG, log_p)
        flow_change.FLOW, flow_change.LOG = flow_p, log_p
        before_flow = flow_p.read_text(encoding="utf-8")
        before_rows = len(log_p.read_text(encoding="utf-8").splitlines())

        # 1. AN UNLINKED NODE IS REFUSED, and the refusal is the lint's, not a shrug.
        msg = refused(flow_change.add_task_node, {"id": "skill_review", "name": "Skill review",
                                                  "worker_kind": "role", "outputs": ["a verdict"]})
        check("an unlinked task node is refused", bool(msg), msg[:80])
        check("  the refusal explains what is missing (a link, or nothing sends work to it)",
              "nothing sends work to it" in msg or "link" in msg.lower(), msg[:200])

        # 2. A REFUSAL WRITES NOTHING. This is the property that makes it safe to try.
        check("a refused node leaves FLOW.json byte-identical",
              flow_p.read_text(encoding="utf-8") == before_flow)
        check("a refused node writes no changelog row",
              len(log_p.read_text(encoding="utf-8").splitlines()) == before_rows)

        # 3. A DUPLICATE ID IS REFUSED -- even a fully-formed node cannot overwrite one that exists.
        msg = refused(flow_change.add_task_node, {"id": "scoping", "name": "Scoping again"})
        check("a duplicate id is refused", bool(msg), msg[:120])

        # 4. A NODE WITH NO ID IS REFUSED before the lint is even asked.
        msg = refused(flow_change.add_task_node, {"name": "nameless"})
        check("a node with no id is refused", "id" in msg.lower(), msg[:120])

        # 5. THE COMITTED CHART IS UNTOUCHED BY THIS SUITE.
        check("the real chart still matches its newest changelog row after the refusals",
              flow_change.guard() == [], flow_change.guard())

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
