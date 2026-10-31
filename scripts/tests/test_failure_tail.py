#!/usr/bin/env python3
"""A failing suite's stored output must be enough to CLASSIFY it (E1a, t8648).

Round 7's classification failed for a reason that was not the data: `record_passes` kept the last FOUR
lines of a failing suite's output, and most of these suites print their tally last ("12 passed,
3 failed"). So for **37 of 318** red suites the cache held no error line at all, and the remaining were a
257-way tail of singletons -- not because the failures are unrelated, but because the stored window cut
the cause off.

That is the failure this session keeps meeting: the record was fine and the instrument was narrow. The
fix is a bounded digest that GUARANTEES the error line is in it --

  * the last `max_lines` lines, because the tail carries the tally, AND
  * the last line matching an error pattern anywhere in the output, if the tail does not already carry
    one, so a suite whose error scrolls past is still classifiable;
  * bounded in characters as well as lines, because this file is rewritten by every sweep.

What this refuses: a digest that is only the last four lines; a digest that grows without a bound; and an
empty output reading as a classification rather than saying it is empty.

Hermetic: `failure_tail` takes text and returns text.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import run_tests  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


TALLY_LAST = "\n".join([
    "  ok   one",
    "  ok   two",
    "Traceback (most recent call last):",
    '  File "x.py", line 3, in <module>',
    "ModuleNotFoundError: No module named 'yaml'",
    "",
    "  2 failure(s)",
    "3 passed, 2 failed",
])

MIXED = "\n".join(
    [f"  ok   case {i}" for i in range(60)]
    + ["AttributeError: 'NoneType' object has no attribute 'strip'"]
    + [f"  ok   case {i}" for i in range(60, 80)]
    + ["80 passed, 1 failed"])


def main():
    f = run_tests.failure_tail

    # 1. THE FINDING: the error line survives even though a tally comes last.
    got = f(TALLY_LAST)
    check("the error line is kept even when the tally is last",
          "No module named 'yaml'" in got, got[-160:])
    check("and the tally is kept too", "3 passed, 2 failed" in got, got[-160:])

    # 2. AN ERROR THAT SCROLLED PAST THE TAIL WINDOW IS STILL KEPT.
    got = f(MIXED)
    check("an error outside the tail window is still kept",
          "AttributeError" in got and "80 passed, 1 failed" in got, got[-200:])

    # 3. BOUNDED BOTH WAYS. This file is rewritten by every sweep.
    long_out = "\n".join(f"line {i} " + "x" * 200 for i in range(500))
    got = f(long_out)
    check("the digest is bounded in characters", len(got) <= 3200, len(got))
    check("the digest is bounded in lines", got.count("\n") <= 41, got.count("\n"))

    # 4. EMPTY OUTPUT SAYS SO, rather than reading as a classification.
    check("an empty output is labelled, not blank", f("") != "", repr(f("")))
    check("whitespace-only output is labelled too", f("  \n\n ") != "", repr(f("  \n\n ")))

    # 5. ORDINARY OUTPUT IS UNCHANGED IN SUBSTANCE: the tail is still the tail.
    got = f("a\nb\nc\n")
    check("a short clean output is kept as-is", got.strip() == "a\nb\nc", repr(got))

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
