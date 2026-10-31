#!/usr/bin/env python3
"""The decay check must read every DIALECT a suite here proves failure in, not just the first one.

On 2026-10-25 "Every test suite proves a failure" went from naming 651 suites to 17 once it learned
`assert` and `sys.exit(<non-zero>)`. The 17 that remained were not broken suites: twelve were unittest
suites (`unittest.main()` exits non-zero when a test fails), four ended in `raise SystemExit(1 if fails
else 0)`, and the last raises `SystemExit(1)` inside its own `check()`. The check could not see any of
them, which is the instrument being wrong rather than the repository.

So this oracle pins the remaining dialects, and one that must NOT count:
  * a TestCase class, and `unittest.main()`             -> proof
  * `unittest.main(exit=False)`                         -> NOT proof: it returns success whatever happens
  * `raise SystemExit(<non-zero>)`                      -> proof (including a conditional argument)
  * and, on the real repository, NO suite left unproven -- which is the state this change reached
    (0 named, from 651 this morning).

Hermetic: fixtures are strings; the one live assertion only reads files.
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


UNITTEST = '''
import unittest
class T(unittest.TestCase):
    def test_x(self):
        self.assertEqual(1, 1)
if __name__ == "__main__":
    unittest.main()
'''

UNITTEST_NO_EXIT = '''
import unittest
class T(unittest.TestCase):
    def test_x(self):
        self.assertEqual(1, 1)
unittest.main(exit=False)
'''

SYSTEM_EXIT = '''
FAILS = []
raise SystemExit(1 if FAILS else 0)
'''

SYSTEM_EXIT_ZERO = '''
raise SystemExit(0)
'''


def main():
    check("a unittest suite proves a failure: unittest.main() exits non-zero",
          cw.suite_proves_a_failure(UNITTEST), "unittest not recognised")
    check("unittest.main(exit=False) is NOT proof -- it cannot fail the build",
          not cw.suite_proves_a_failure(UNITTEST_NO_EXIT), "exit=False was accepted as proof")
    check("raise SystemExit(1 if fails else 0) proves a failure",
          cw.suite_proves_a_failure(SYSTEM_EXIT), "SystemExit not recognised")
    check("raise SystemExit(0) is NOT proof", not cw.suite_proves_a_failure(SYSTEM_EXIT_ZERO),
          "a guaranteed-success exit was accepted")

    # THE REAL REPOSITORY. If this ever fails, one suite cannot be told from one that cannot fail --
    # read it, do not add it to an exemption list.
    live = ROOT / "scripts" / "tests"
    tokens = ('"FAIL"', '"REFUSE"', "refused(", "Denied", "exit 1", "Error)", "AuthError", "raises",
              "pytest.raises", "returncode != 0", "returncode == 1", "returncode == 2",
              "returncode == 3", "NOT WRITTEN")
    still = [f.name for f in sorted(live.glob("test_*.py"))
             if not any(n in f.read_text(errors="replace") for n in tokens)
             and not cw.suite_proves_a_failure(f.read_text(errors="replace"), str(f))]
    check("no suite in the real repository is left unproven", still == [], still[:8])

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
