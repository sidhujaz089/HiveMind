#!/usr/bin/env python3
"""Fail-first oracle for t1889: the ceiling is a BUDGET, not a policy.

Jas, 2026-10-25: *"Make the burn down budget code and not policy. Policies will be broken. If the
code is a problem, I would rather change it later."*

`check_workspace` already refuses to let the COUNT rise (t1589) — but a count is a number a person
can raise, and `scripts/library.py` appends a red suite to the list by itself. A budget is different:
**a new failing suite may enter only if another one leaves.** Swap, or fall. Growth is not a rule that
can be broken politely; it is a comparison of two lists that refuses.

What this cannot see: a hand-edited list that swaps a real suite for a fake one. That is why the check
also reports the added and removed names on every run — a swap is named, not silent.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import check_workspace as cw  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    check("the budget is a function", hasattr(cw, "ceiling_budget"), True)
    if not hasattr(cw, "ceiling_budget"):
        print(f"{PASSED} passed, {FAILED} failed")
        return 1

    # 1. A pure addition is GROWTH -- the thing the budget exists to refuse.
    b = cw.ceiling_budget(["a.py", "b.py"], ["a.py", "b.py", "c.py"])
    check("adding one suite is growth", b["verdict"], "grew")
    check("  and the added name is named", b["added"], ["c.py"])
    check("  with nothing removed", b["removed"], [])

    # 2. One out, one in is a SWAP: allowed, and named so the swap is visible.
    b = cw.ceiling_budget(["a.py", "b.py"], ["a.py", "c.py"])
    check("one out and one in is a swap", b["verdict"], "swap")
    check("  the swap names both sides", (b["added"], b["removed"]), (["c.py"], ["b.py"]))

    # 3. Removing without adding is the point of the exercise.
    b = cw.ceiling_budget(["a.py", "b.py", "c.py"], ["a.py", "b.py"])
    check("one fewer is a fall", b["verdict"], "fell")
    check("  and names what left", b["removed"], ["c.py"])

    # 4. No change, and a first run with no previous list, are neither growth nor a gap.
    check("the same list is unchanged", cw.ceiling_budget(["a.py"], ["a.py"])["verdict"], "same")
    check("no previous list cannot be growth", cw.ceiling_budget([], ["a.py", "b.py"])["verdict"], "same")
    check("  and a first list is reported as its size",
          cw.ceiling_budget([], ["a.py", "b.py"])["count_now"], 2)

    # 5. Two in, two out is a swap, not growth: the budget is about the population, not the edits.
    b = cw.ceiling_budget(["a.py", "b.py", "x.py"], ["a.py", "c.py", "d.py"])
    check("two swapped for two is a swap", b["verdict"], "swap")

    # 6. The check that uses it is wired into the run.
    import inspect
    check("the budget check runs with the others",
          "check_ceiling_budget" in inspect.getsource(cw.Checker.run), True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
