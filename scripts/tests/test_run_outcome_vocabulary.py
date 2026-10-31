#!/usr/bin/env python3
"""Fail-first oracle for t1587: one outcome vocabulary per lane, and nothing unclassified.

WHY. `workspace/agent-runs.jsonl` writes `outcome` from two different vocabularies. The coder
lane writes a VERDICT (ACCEPTED, TESTS-FAILED, REFUSED-TO-START...); the console lane writes
the model's TRANSPORT reason (stop, length, timeout, http-500...). Measured 2026-10-24: 844
coder rows over 14 upper-case verdicts and 5,603 ask_q16 rows over 6 lower-case reasons -- so
70% of the ledger's `outcome` field means "the model finished", which makes any success rate
computed from it meaningless as written.

The fix is not to rewrite history and not to coerce the transport reason into a tidy word
(`ask_q16.py` deliberately stopped doing that, because a reason nobody has invented yet must
surface on its own). It is to say which vocabulary a value belongs to, in one place, and to
FAIL when a value belongs to neither -- so a new outcome is classified the day it appears
instead of quietly polluting a percentage.

Cases are synthetic: the classifier is a pure function of the value.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import reviewq  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    check("the verdict vocabulary is declared", hasattr(reviewq, "PIPELINE_OUTCOMES"), True)
    check("  and the transport vocabulary is declared", hasattr(reviewq, "TRANSPORT_OUTCOMES"), True)
    check("  and the classifier is one function", hasattr(reviewq, "outcome_class"), True)
    if not all(hasattr(reviewq, n) for n in ("PIPELINE_OUTCOMES", "TRANSPORT_OUTCOMES", "outcome_class")):
        print(f"{PASSED} passed, {FAILED} failed")
        return 1

    # 1. The two vocabularies are disjoint: a value in both would be unclassifiable.
    both = reviewq.PIPELINE_OUTCOMES & reviewq.TRANSPORT_OUTCOMES
    check("the two vocabularies do not overlap", sorted(both), [])

    # 2. The measured members of each, from the live ledger on 2026-10-24.
    for v in ("ACCEPTED", "TESTS-FAILED", "REFUSED-TO-START", "ACCEPTED-NOT-WRITTEN", "BAD-SYNTAX"):
        check(f"a verdict: {v}", reviewq.outcome_class({"outcome": v, "tool": "coder"}), "pipeline")
    for v in ("stop", "length", "timeout", "http-500", "http-404", "lease_held",
              "finished-from-reasoning"):
        check(f"a transport reason: {v}", reviewq.outcome_class({"outcome": v, "tool": "ask_q16"}), "transport")

    # 3. A value in neither is UNCLASSIFIED -- not "transport", not ignored. This is the whole
    #    point: a new outcome must be named, not absorbed.
    check("an invented value is unclassified",
          reviewq.outcome_class({"outcome": "SOMETHING-NEW", "tool": "coder"}), "unclassified")
    check("a missing outcome is 'unrecorded', not a silently accepted value",
          reviewq.outcome_class({"tool": "coder"}), "unrecorded")

    # 4. The classifier does not depend on `tool` alone: a lower-case value from the coder lane
    #    is still transport, and an upper-case value from the console lane is still a verdict.
    #    (The vocabulary is the contract; `tool` is only how it was measured.)
    check("the vocabulary decides, not the tool name",
          reviewq.outcome_class({"outcome": "stop", "tool": "coder"}), "transport")
    check("  in both directions",
          reviewq.outcome_class({"outcome": "ACCEPTED", "tool": "ask_q16"}), "pipeline")

    # 5. A delivery is a pipeline verdict that says the change exists. `ACCEPTED-DRYRUN` is
    #    NOT one: nothing was written, so counting it as a delivery would overstate the line.
    check("ACCEPTED is a delivery", reviewq.is_delivery({"outcome": "ACCEPTED"}), True)
    check("ACCEPTED-DRYRUN is not", reviewq.is_delivery({"outcome": "ACCEPTED-DRYRUN"}), False)
    check("TESTS-FAILED is not", reviewq.is_delivery({"outcome": "TESTS-FAILED"}), False)
    check("a transport reason is not", reviewq.is_delivery({"outcome": "stop"}), False)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
