#!/usr/bin/env python3
"""A patch that satisfies X while breaking a suite asserting NOT-X is IMPOSSIBLE, not failed (A8, 2026-10-30).

The brief: "A8 an oracle-conflict CLASSIFIER: a patch that satisfies X breaks a suite asserting NOT-X on the same
symbol -> class oracle-conflict, route to Technical, never retry. Not a pre-build static check (t1432's target was
not green when the collateral failed)."

t1432 is the shape: its target demands a MAX_TOKENS_FIRST cap while two older suites assert first_budget returns all
250,144 tokens. No patch can satisfy both, so every attempt spends a build and escalates -- the funnel the RCA
found. The classifier runs on the OUTCOME (target green, collateral red), which is why it cannot be a pre-build
check.

WHAT IT CAN AND CANNOT KNOW. It does not parse English. It compares the LITERAL a suite asserts about a symbol --
`assert first_budget(...) == 250144` against `... == capped` -- and calls it a conflict when the same symbol carries
two different literals. When the literals cannot be extracted it says `oracle-overlap` instead, which still routes
to Technical but claims less: overlap is not proof of contradiction, and a classifier that overstates is how a
queue gets parked for the wrong reason.

RED ON MAIN: scripts/oracle_conflict.py does not exist.
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import oracle_conflict as oc          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    target = d / "test_target.py"
    target.write_text("def test_x():\n    assert first_budget('hi') == 2048\n", encoding="utf-8")
    collateral = d / "test_collateral.py"
    collateral.write_text("def test_x():\n    assert first_budget('hi') == 250144\n", encoding="utf-8")
    other = d / "test_other.py"
    other.write_text("def test_y():\n    assert unrelated('hi') == 7\n", encoding="utf-8")

    got = oc.classify(["first_budget"], [target], [collateral, other])
    check("the same symbol asserted two different ways is a CONFLICT", got["class"] == "oracle-conflict", str(got))
    check("  and it names the symbol", got["symbol"] == "first_budget", str(got))
    check("  and it names BOTH suites, so a reader can check it", set(got["suites"]) ==
          {str(collateral), str(target)}, str(got.get("suites")))
    check("  and it quotes the two literals", sorted(got["values"]) == ["2048", "250144"], str(got.get("values")))
    check("  and it does not blame the suite that never mentions the symbol",
          str(other) not in str(got.get("suites")), str(got.get("suites")))

    # the same literal on both sides is AGREEMENT, not conflict
    agree = d / "test_agree.py"
    agree.write_text("def test_x():\n    assert first_budget('hi') == 2048\n", encoding="utf-8")
    check("agreement is not a conflict", oc.classify(["first_budget"], [target], [agree])["class"] != "oracle-conflict",
          str(oc.classify(["first_budget"], [target], [agree])))

    # a symbol with no comparable literal is OVERLAP: routed, but not called a contradiction
    vague = d / "test_vague.py"
    vague.write_text("def test_x():\n    first_budget('hi')\n    assert something_else\n", encoding="utf-8")
    got = oc.classify(["first_budget"], [target], [vague])
    check("a shared symbol with no literal is called OVERLAP, not a conflict", got["class"] == "oracle-overlap", str(got))
    check("  and it still names the symbol and the suite", got["symbol"] == "first_budget" and
          str(vague) in got["suites"], str(got))

    # nothing shared at all is not this class
    check("an untouched symbol is neither", oc.classify(["nobody_touches_me"], [target], [collateral])["class"] is None,
          str(oc.classify(["nobody_touches_me"], [target], [collateral])))
    check("no evidence at all is None, not a guess", oc.classify([], [], [])["class"] is None,
          str(oc.classify([], [], [])))

print("test_oracle_conflict:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
