#!/usr/bin/env python3
"""t1118: when a line-range edit is refused for cutting a multi-line string, the refusal
must be actionable, not just a statement of the rule.

Filed by the Q16 supervisor from p497-t1101-task-routing (2026-10-13): the brief named
lines 32-34 of scripts/task_routing.py (a comment and the DEFAULT = {"roles": {...}
assignment), and in the builder's copy of the file those lines sit inside the module
docstring, which spans lines 2-35. coder.apply_ops refused -- correctly -- and the message
gave the rule only: "the range must cover the whole string (lines 2-35) or not cross it".
A model that follows that advice deletes the module docstring. The brief's line numbers
were wrong and the message gave the model no way to find the right ones.

So the refusal must also show WHAT IS THERE: the string's own first and last lines, the
lines the edit asked to replace, and -- when the edit carries replacement text -- the line
of the file where that text actually lives, saying so plainly when it appears nowhere.

Run as: python3 scripts/tests/test_coder_range_hint.py   (pytest is not installed).
"""
import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

PASSED = FAILED = 0

# m.py, with its true line numbers marked in comments so the arithmetic is checkable:
#   1  """Module docstring line one
#   2  filler two
#   3  filler three
#   4  filler four
#   5  """
#   6  DEFAULT = {"roles": {}}
#   7  OTHER = 1
DOCSTRING_SRC = (
    '"""Module docstring line one\n'
    'filler two\n'
    'filler three\n'
    'filler four\n'
    '"""\n'
    'DEFAULT = {"roles": {}}\n'
    'OTHER = 1\n'
)


def report(name, cond, detail=""):
    global PASSED, FAILED
    ok = bool(cond)
    PASSED += 1 if ok else 0
    FAILED += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   -- {detail}"))


def edit(src, lo, hi, new):
    """One line-range op against m.py in a fresh temp dir -> (ok, why, before, after)."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "m.py"
        f.write_text(src, encoding="utf-8")
        before = f.read_text(encoding="utf-8")
        ok, why = coder.apply_ops(Path(d), [{"path": "m.py", "from": lo, "to": hi, "new": new}])
        return ok, why or "", before, f.read_text(encoding="utf-8")


def main():
    print("A range that cuts the module docstring is refused, and the refusal shows the file:")
    ok, why, before, after = edit(DOCSTRING_SRC, 2, 4, 'DEFAULT = {"roles": {}}')
    report("the refusal still fires (guard not weakened)", ok is False, f"ok={ok!r}  why={why!r}")
    report("and the file is left byte-identical", after == before, f"after={after!r}")
    report("and it still names the string's span",
           "lines 1-5" in why, f"why={why!r}")
    report("and it shows the string's first line, verbatim",
           '"""Module docstring line one' in why, f"why={why!r}")
    report("and it shows the string's last line, verbatim",
           "filler four" in why and '"""' in why, f"why={why!r}")
    report("and it shows every line the edit asked to replace",
           "filler two" in why and "filler three" in why and "filler four" in why,
           f"why={why!r}")
    report("and it points at the line where the replacement text actually lives",
           "line 6" in why and 'DEFAULT = {"roles": {}}' in why, f"why={why!r}")
    report("and the pointer names only lines of this file",
           "line 99" not in why, f"why={why!r}")

    print("A replacement that exists nowhere in the file is said to, not guessed at:")
    ok2, why2, before2, after2 = edit(DOCSTRING_SRC, 2, 4, "MISSING_TOKEN = 42")
    report("the refusal fires", ok2 is False, f"ok={ok2!r}")
    report("and it says plainly that no line in the file matches the text",
           "no line in this file matches" in why2, f"why={why2!r}")
    report("and it does not point at the target of the other case",
           "line 6" not in why2, f"why={why2!r}")
    report("and the file is untouched", after2 == before2, f"after={after2!r}")

    print("The guard keeps its contract: a range covering the whole string applies.")
    ok3, why3, before3, after3 = edit(DOCSTRING_SRC, 1, 5, '"""short docstring"""')
    report("a whole-string replacement is accepted", ok3 is True, f"why={why3!r}")
    report("and the docstring is replaced with the code below it intact",
           after3 == '"""short docstring"""\nDEFAULT = {"roles": {}}\nOTHER = 1\n',
           f"after={after3!r}")
    report("and the result parses", bool(ast.parse(after3)), f"after={after3!r}")

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
