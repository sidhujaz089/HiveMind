#!/usr/bin/env python3
"""Fail-first oracle for t3196: the auto-merge gate was narrower than the risk it covers.

`auto_merge.py` merges a branch unread only when every condition is mechanical. Condition 1 was
"exactly one file, and it is not a test", which refuses a zero-risk build that touches the two files
its own brief names -- so the automatic path covered less than the class it exists for, and the rest
went to a person.

Two widenings, both still mechanical, plus one fence:

  FILES   when the brief carries a `FILES:` line, every changed file must be ON it (and the oracle is
          never among them); with no FILES line the old one-file rule stands unchanged, so no existing
          branch is judged differently.
  POLICY  a hunk that ADDS or REMOVES a line carrying the POLICY marker goes to a person whatever else
          holds: a one-file, symbol-narrow, red->green change can still flip a default that IS a policy
          (a role's model, a pause flag, a retention window), and no amount of narrowness makes that a
          mechanical decision.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import auto_merge as am  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    for attr in ("brief_files", "files_allowed", "policy_lines"):
        check(f"{attr} exists", hasattr(am, attr), True)
    if not all(hasattr(am, a) for a in ("brief_files", "files_allowed")):
        print(f"{PASSED} passed, {FAILED} failed")
        return 1

    # ---- the FILES line is read, and only when it is there -------------------------------
    brief = ("CHUNK p1-t1\nFILES: scripts/one.py, scripts/two.py\nSYMBOLS: alpha, beta\n")
    check("a FILES line yields its paths", am.brief_files(brief),
          ["scripts/one.py", "scripts/two.py"])
    check("a brief with no FILES line yields []", am.brief_files("SYMBOLS: alpha\n"), [])
    check("backticks around a path are tolerated",
          am.brief_files("FILES: `scripts/a.py`\n"), ["scripts/a.py"])

    # ---- no FILES line: the old rule, unchanged ------------------------------------------
    check("one file and no FILES line is allowed",
          am.files_allowed(["scripts/one.py"], "scripts/tests/test_x.py", [])[0], True)
    ok, why = am.files_allowed(["scripts/one.py", "scripts/two.py"], "scripts/tests/test_x.py", [])
    check("two files and no FILES line is refused", ok, False)
    check("  and the reason says why", "exactly one" in why, True)

    # ---- a FILES line: every changed file must be on it ----------------------------------
    named = ["scripts/one.py", "scripts/two.py"]
    check("two files, both named, is allowed",
          am.files_allowed(["scripts/one.py", "scripts/two.py"], "scripts/tests/test_x.py", named)[0],
          True)
    check("one file, named, is allowed (the shape most builds have)",
          am.files_allowed(["scripts/one.py"], "scripts/tests/test_x.py", named)[0], True)
    ok, why = am.files_allowed(["scripts/one.py", "scripts/elsewhere.py"],
                               "scripts/tests/test_x.py", named)
    check("a file the brief does not name is refused", ok, False)
    check("  and names the offender", "scripts/elsewhere.py" in why, True)

    # ---- the oracle is never the builder's to change, named or not -----------------------
    ok, why = am.files_allowed(["scripts/one.py", "scripts/tests/test_x.py"],
                               "scripts/tests/test_x.py", ["scripts/one.py", "scripts/tests/test_x.py"])
    check("changing the oracle itself is refused even when the brief names it", ok, False)
    check("  and says the oracle is not the model's to change", "oracle" in why, True)

    # ---- the POLICY fence -----------------------------------------------------------------
    check("the marker is POLICY:", am.POLICY_MARK, "POLICY:")
    added = ["    x = 1", "    # POLICY: the model guard may not shed the model first", "    y = 2"]
    check("a policy line is found in added text", am.policy_lines_from(["x", *added[1:2]]),
          [added[1]])
    check("no policy line means no finding", am.policy_lines_from(["    x = 1"]), [])

    # ---- and the gate really uses both -----------------------------------------------------
    src = (REPO / "scripts" / "auto_merge.py").read_text(encoding="utf-8")
    body = src[src.index("def judge("):src.index("def merge(")]
    check("judge() asks files_allowed instead of counting files", "files_allowed(" in body, True)
    check("judge() refuses on a policy line", "policy_lines(" in body, True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
