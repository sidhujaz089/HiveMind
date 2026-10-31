#!/usr/bin/env python3
"""t4388: "every test suite proves a failure" must see the suites that prove one with asserts.

The check named 651 suites on 2026-10-25 -- two thirds of the repository -- because it only recognised
a quoted token ("FAIL", "raises", "refused("...) or a report(label, rc, <non-zero int>) call. A suite
written the common way here -- plain `assert`s and `sys.exit(1 if bad else 0)` -- was invisible to it,
so a WARN naming 651 files told nobody anything and could never be cleared a suite at a time.

Jas: "Make it visible. The heuristic learns to see sys.exit(<non-zero>)". That is two additions, and
both are structural (parsed with ast), not textual, so a suite cannot pass by containing the word:

  * an `assert` statement   -- the suite can fail, which is the whole question;
  * `sys.exit(<non-zero>)`  -- including a non-constant argument, because `sys.exit(1 if bad else 0)`
                               is the shape this repository actually uses.

A suite with neither, and no token, is still named. The point is to make the list SHORT AND TRUE, not
to empty it: the guard case below is a print-only script that must keep being flagged.

Hermetic: the fixtures are strings; nothing is executed.
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


PLAIN = '''
import sys
count = 0
count += 1
assert count == 1, "one"
if count != 1:
    sys.exit(1)
'''

CONDITIONAL_EXIT = '''
import sys
bad = 0
def main():
    return 1 if bad else 0
sys.exit(main())
'''

REPORT_RC = '''
def report(label, got, want):
    print(label, got, want)
rc = 1
report("a failure is refused", rc, 1)
report("a length is not a return code", len("abc"), 1)
'''

PRINT_ONLY = '''
print("this script has opinions but cannot fail")
x = 1 + 1
print(x)
'''

EXIT_ZERO_ONLY = '''
import sys
print("all good")
sys.exit(0)
'''


def main():
    check("a suite of plain asserts proves a failure", cw.suite_proves_a_failure(PLAIN), PLAIN[:40])
    check("sys.exit(1 if bad else 0) proves a failure",
          cw.suite_proves_a_failure(CONDITIONAL_EXIT), CONDITIONAL_EXIT[:40])
    check("report(label, rc, 1) still proves a failure (the old rule)",
          cw.suite_proves_a_failure(REPORT_RC), "the pre-existing rule was lost")
    check("a script that cannot fail is still named", not cw.suite_proves_a_failure(PRINT_ONLY),
          "print-only was accepted as proving a failure")
    check("sys.exit(0) alone is not proof -- it cannot fail",
          not cw.suite_proves_a_failure(EXIT_ZERO_ONLY), "a guaranteed-success exit was accepted")
    check("broken source is not a pass", not cw.suite_proves_a_failure("def (:\n"))

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
