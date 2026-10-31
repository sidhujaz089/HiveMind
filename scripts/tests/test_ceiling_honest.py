#!/usr/bin/env python3
"""The ceiling may only contain suites that actually fail (t7519).

`workspace/test-failures-ceiling.json` lists the suites ALLOWED to fail. "Allowed to fail" is a
budget for work not yet done -- so a suite on that list that passes is not a defect in the suite, it
is the LIST lying about what is left. On 2026-10-26 that was 102 of 456: a quarter of the declared
debt was already paid and nobody had claimed it, because the count was checked (t1589) and the
HONESTY of the list was not.

Why this needs to be a check and not a measurement: a green suite on the list is invisible. It does
not fail anything, it does not warn, and every report that reads the list inherits the overstatement.
The claim "354 suites are failing" was quoted for two sessions while the number on the list was 456.

The decision this encodes, and it is Jas's (2026-10-26): deleting a suite from the list makes the
ceiling STRICTER, so it must be proven by running it. Dropping the 102 and deleting their
`suite-cache.json` entries means the next `run_tests.py --cache` sweep actually executes them,
instead of reusing a cached pass -- that is the proof, and it needs no forbidden full-suite run.

  * a suite on the list with a latest verdict of 0  -> it passes: remove it from the list
  * a suite on the list with no verdict at all      -> nothing proves it fails: run it first
  * a suite on the list with a non-zero verdict     -> correctly declared, nothing to say

Hermetic: the pure function takes the names and the verdicts, so nothing here reads the live store.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    # 1. A DECLARED-FAILING SUITE THAT PASSES IS THE FINDING. This is the whole suite: without it
    #    the check is an expensive no-op.
    v = cw.ceiling_honesty(["test_green.py"], {"test_green.py": 0})
    check("a suite on the ceiling whose latest verdict is 0 is reported as green",
          v["green"] == ["test_green.py"], v)
    check("and it is not counted as red", v["red"] == 0, v)

    # 2. A suite that really is failing is left alone -- the common case, and the one that must not
    #    produce noise or the check gets ignored (G10).
    v = cw.ceiling_honesty(["test_red.py"], {"test_red.py": 1})
    check("a suite that fails is not reported", (v["green"], v["unrecorded"], v["red"]) == ([], [], 1), v)

    # 3. ABSENCE IS NOT AGREEMENT. No verdict means nothing proves it fails; that is a different
    #    sentence from "it passes", because the repair is different (run it vs remove it).
    v = cw.ceiling_honesty(["test_never_run.py"], {})
    check("a suite on the ceiling with no recorded verdict is reported as unrecorded",
          v["unrecorded"] == ["test_never_run.py"], v)
    check("and no verdict is NOT silently counted as green", v["green"] == [], v)

    # 4. An empty list is clean -- the ceiling may reach zero, and that is not an error.
    v = cw.ceiling_honesty([], {})
    check("an empty ceiling is clean", (v["green"], v["unrecorded"], v["red"]) == ([], [], 0), v)

    # 5. Order is deterministic, so a failure names the same suites twice in a row and a diff of two
    #    runs is readable.
    v = cw.ceiling_honesty(["b.py", "a.py", "c.py"], {"a.py": 0, "b.py": 0, "c.py": 2})
    check("greens come out sorted", v["green"] == ["a.py", "b.py"], v)

    # 6. It reads the LATEST verdict, not the first -- and that is the caller's job, so a mapping with
    #    one entry per suite is all it may assume. Passing a non-mapping is a caller bug, not silence.
    try:
        cw.ceiling_honesty(["a.py"], None)
        check("a caller passing no verdicts at all is refused, not treated as all-clear", False,
              "it returned rather than raising")
    except (TypeError, AttributeError):
        check("a caller passing no verdicts at all is refused, not treated as all-clear", True)

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
