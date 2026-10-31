#!/usr/bin/env python3
"""Fail-first oracle for t1589: the test-failure ceiling may fall and must not rise quietly.

WHY. `workspace/test-failures-ceiling.json` names 457 suites that are allowed to fail. Measured
2026-10-24: 457 distinct names, none of them for a suite that is missing, and **nothing anywhere
refuses growth** -- `scripts/library.py:323` appends a red suite to the file by itself, as do the
bridge and its trigger. So the only direction the number moves on its own is UP, and "457 of 914"
became the state of the art by nobody deciding it.

This is the same defect AGENTS.md already fixed for lessons, in its own words: adding an
unguarded lesson "costs you either mechanising an older one, or raising a number in a committed
file on purpose". The test ceiling had no such number.

So: a committed count, a check that fails when the list grows past it, and a flag that lowers it.
Visibility is half the point -- the count is a sum, and a sum hides where the debt sits, so the
report says which groups own it.

Pure function, synthetic input: nothing here reads the live ceiling.
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
    check("the ceiling report is a function", hasattr(cw, "ceiling_report"), True)
    if not hasattr(cw, "ceiling_report"):
        print(f"{PASSED} passed, {FAILED} failed")
        return 1

    names = ["test_coder_a.py", "test_coder_b.py", "test_scope_lint_x.py", "test_session_x.py",
             "test_session_y.py", "test_session_z.py", "helper_without_prefix.py"]

    # 1. The count, and the direction that matters.
    r = cw.ceiling_report(names, ratchet=7)
    check("the count is the number of declared suites", r["count"], 7)
    check("  and it is not over the ratchet", r["over"], 0)
    r = cw.ceiling_report(names, ratchet=5)
    check("two names past the ratchet is OVER by two", r["over"], 2)
    r = cw.ceiling_report(names[:-2], ratchet=7)
    check("below the ratchet is not a failure", r["over"], 0)
    check("  and the headroom is reported, so it can be ratcheted", r["headroom"], 2)

    # 2. Visibility: a sum hides where the debt sits. (Measured on the FULL list -- the case
    #    above drops two names, which changes the distribution, and my first draft asserted
    #    against that anyway.)
    full = cw.ceiling_report(names, ratchet=7)
    check("the largest group is named first", full["groups"][0], ("session", 3))
    check("  and the whole distribution is there", dict(full["groups"])["coder"], 2)
    check("  a name with no group prefix is its own bucket", dict(full["groups"])["other"], 1)

    # 3. Names for suites that are not on disk are DEBT THAT IS PAID OR A LIE, either way named.
    r = cw.ceiling_report(["test_coder_a.py", "test_gone_forever.py"], ratchet=2,
                          existing={"test_coder_a.py"})
    check("an absent suite is reported", r["absent"], ["test_gone_forever.py"])

    # 4. No ratchet file is not a pass.
    r = cw.ceiling_report(names, ratchet=None)
    check("a missing ratchet is 'no ratchet', not zero", r["ratchet"], None)
    check("  and over is None when there is nothing to compare", r["over"], None)

    # 5. The lowering flag exists and can only lower.
    import inspect
    src = inspect.getsource(cw.main)
    check("there is a --ratchet-ceiling flag", "--ratchet-ceiling" in src, True)
    check("  and it refuses to raise",
          "may not raise" in src or "only lowers" in src.lower() or "cannot raise" in src.lower(), True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
