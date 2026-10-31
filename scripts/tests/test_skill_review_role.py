#!/usr/bin/env python3
"""Who reviews a skill draft is a ROLE's task, not a rule about who wrote it (P1b, t8715).

Jas, 2026-10-26: *"I don't care for the rule that a model can't approve its own draft. It is stale... Really
the draft review should be a task and that task is assigned at the role level on the role tab. In the
future I can remove the review task from a role and keep it on others."*

The rule being replaced is prose in `skill_change.py`: a draft goes to the Manager *"for a draft the
Manager did not write itself (no model reviews its own draft)"*, enforced by comparing the proposer's
identity. That is a policy about people expressed as a string comparison.

What it becomes: `skill_change` asks the ROLE STORE which roles hold `skill_review`, and routes there. Two
consequences, both his:

  * a role that holds it reviews, including its own work -- no identity comparison anywhere;
  * a role that does NOT hold it gets no review at all, and removing the task from a role changes the
    behaviour with NO code change.

AND A SAFE FALLBACK, which is most of this suite. No role holds `skill_review` yet (the chart node is still
waiting on its links, t8712), and there is a live draft -- t7241 -- sitting in that pipeline. So while no
role holds the task, the old rule stands unchanged. The new behaviour switches on only when a role is
given the task, which is a Roles-tab action Jas makes.

What this refuses:

  * a review routed to a role that does not hold the task;
  * a proposal by a holder being sent to itself for review (that is the stale rule coming back);
  * an unreadable role store silently meaning "nobody reviews" -- it falls back to today's rule and SAYS SO;
  * and any behaviour change while no role holds the task: default must equal today.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import role_store  # noqa: E402
import skill_change  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def store_with(**task_lists):
    d = {"roles": {}}
    for role, tasks in task_lists.items():
        d["roles"][role] = {"name": role, "kind": "model", "what": "x",
                            "tasks": [{"task": t, "interrupt": False} for t in tasks]}
    return d


def main():
    real = role_store.STORE

    # 1. DEFAULT EQUALS TODAY, because no role holds the task yet and t7241 is live in that pipeline.
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "ROLES.json"
        p.write_text(json.dumps(store_with(manager=["merge_review"], dsh=["console"])), encoding="utf-8")
        role_store.STORE = p
        check("no role holds skill_review, so the holders list is empty", skill_change.review_holders() == [],
              skill_change.review_holders())
        check("a non-manager draft still goes to the Manager for review (today's behaviour, unchanged)",
              skill_change.review_from("dsh-harness") == "manager", skill_change.review_from("dsh-harness"))
        check("and the Manager's own draft still needs no review",
              skill_change.review_from("manager") == "", skill_change.review_from("manager"))

    # 2. A ROLE HOLDS IT: the review goes there, and the proposer's identity stops mattering.
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "ROLES.json"
        p.write_text(json.dumps(store_with(manager=["merge_review"], swift=["skill_review"])), encoding="utf-8")
        role_store.STORE = p
        check("the holder is found from the role store", skill_change.review_holders() == ["swift"],
              skill_change.review_holders())
        check("a draft by another role is reviewed BY THE HOLDER", skill_change.review_from("dsh-harness") == "swift",
              skill_change.review_from("dsh-harness"))
        check("and a draft by the holder needs no one else's review -- it may approve its own",
              skill_change.review_from("swift") == "", skill_change.review_from("swift"))
        check("the manager is not special any more", skill_change.review_from("manager") == "swift",
              skill_change.review_from("manager"))

    # 3. MOVING THE TASK CHANGES THE BEHAVIOUR WITH NO CODE CHANGE -- the whole point for Jas.
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "ROLES.json"
        p.write_text(json.dumps(store_with(swift=["skill_review"], claude=["skill_review"])), encoding="utf-8")
        role_store.STORE = p
        check("two holders are both found", skill_change.review_holders() == ["claude", "swift"],
              skill_change.review_holders())

    # 4. AN UNREADABLE STORE FALLS BACK AND SAYS SO -- never "nobody reviews" by accident.
    with tempfile.TemporaryDirectory() as tmp:
        role_store.STORE = Path(tmp) / "missing.json"
        check("an unreadable role store falls back to today's rule",
              skill_change.review_from("dsh-harness") == "manager", skill_change.review_from("dsh-harness"))

    role_store.STORE = real
    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
