#!/usr/bin/env python3
"""The dispatcher must recognise its OWN assignments when needs: holds a tier.

t9540. `route_waiting` compares the task's `needs:` against the role it assigned it to:
`now == prev["to"]`. With needs holding "technical" and the role being "claude", that comparison is false for
EVERY task, so the scheduler treats its own work as someone else's and never acts on it -- and the release
path (`assignee(needs) == mine[tid]["to"]`) never fires either. Both go through holders_of() now, which
resolves a tier to its holders and lets a legacy role key name itself.

The property pinned here is EQUIVALENCE: whatever the two comparisons did with the old vocabulary they must
still do with the tier that replaced it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import dispatch_loop as D
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    # The mapping itself.
    check("a tier resolves to its holders: technical -> claude",
          "claude" in D.holders_of("technical"), sorted(D.holders_of("technical")))
    check("  rework -> manager", "manager" in D.holders_of("rework"), sorted(D.holders_of("rework")))
    check("  vision -> jas", "jas" in D.holders_of("vision"), sorted(D.holders_of("vision")))
    check("a legacy role key still names itself", D.holders_of("claude") == {"claude"},
          sorted(D.holders_of("claude")))
    check("production has no role holders", D.holders_of("production") == set(),
          sorted(D.holders_of("production")))

    # THE EQUIVALENCE THE TWO COMPARISONS DEPEND ON.
    for tier, role in (("technical", "claude"), ("rework", "manager"), ("vision", "jas")):
        as_tier = role in (D.holders_of(tier) | {tier})
        as_role = role in (D.holders_of(role) | {role})
        check(f"the scheduler recognises its own assignment under {tier!r} as it did under {role!r}",
              as_tier == as_role == True, f"tier: {as_tier}, role: {as_role}")

    # AND THE NEGATIVE: an assignment to a DIFFERENT role is still somebody else's.
    check("an assignment to another role is still not ours",
          "claude" not in (D.holders_of("rework") | {"rework"}), "")
    print("test_dispatch_holds_its_own_assignments:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
