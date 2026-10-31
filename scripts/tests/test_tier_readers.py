#!/usr/bin/env python3
"""A `needs:` TIER must still reach a role's queue -- the migration silently stopped it doing so.

t9540, after the needs: migration. This is the failure I caused and Claude caught: assigned_worker.pending()
tested `who in model_roles(d)`, and once needs holds a tier (rework / technical / vision) that is false for
every task, so the Manager's queue returned NOTHING while 57 of its items sat on disk (measured:
pending() returned 0 after the migration and 57 with this fix). Claude's requirement, in its words: "each
reader must resolve a tier to its holders (your escalate_all) first, with a test that a rework item still
reaches the Manager's queue".

The mapper accepts BOTH vocabularies on purpose: a half-migrated store must read correctly, and a role key
still names itself.
"""
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import role_store as R
    import assigned_worker as A
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    d = copy.deepcopy(R.load())
    check("a tier resolves to its holders: rework -> manager", R.tier_roles("rework", d) == ["manager"],
          R.tier_roles("rework", d))
    # A TIER RESOLVES TO ALL ITS HOLDERS, and this asserted one name. On 2026-10-30 the Harness role was given
    # technical escalations as well as Claude, so the tier has two holders and the equality became false -- while
    # the case three lines below already asserts that a two-holder tier is the normal case. The property is
    # "claude holds technical", not "claude is the only one who does".
    check("  technical is held by claude", "claude" in R.tier_roles("technical", d), R.tier_roles("technical", d))
    check("  vision -> jas", R.tier_roles("vision", d) == ["jas"], R.tier_roles("vision", d))
    check("a ROLE KEY still names itself (a half-migrated store reads correctly)",
          R.tier_roles("manager", d) == ["manager"], R.tier_roles("manager", d))
    check("production is the LINE, not a role queue", R.tier_roles("production", d) == [], "")
    check("  and so is empty", R.tier_roles("", d) == [], "")

    # THE REQUIREMENT ITSELF: a rework item reaches the Manager's QUEUE.
    pending = A.pending(d)
    rework_ids = [tid for tid, who in pending if who == "manager"]
    check("a rework item still reaches the Manager's queue", bool(rework_ids),
          f"pending() returned {len(pending)} rows, managers: {rework_ids[:3]}")
    check("  the queue is not empty after the migration", len(pending) > 0, f"{len(pending)} rows")

    # TWO HOLDERS MUST NOT DOUBLE THE WORK.
    d2 = copy.deepcopy(d)
    d2["roles"]["dsh"]["escalations"] = ["technical"]
    seen = {}
    for tid, who in A.pending(d2):
        seen[tid] = seen.get(tid, 0) + 1
    check("a two-holder tier offers each task ONCE, not once per holder",
          all(n == 1 for n in seen.values()), str({k: v for k, v in seen.items() if v > 1})[:200])
    print("test_tier_readers:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
