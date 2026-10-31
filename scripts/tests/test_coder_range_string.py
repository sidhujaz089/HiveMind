#!/usr/bin/env python3
"""t469 option B: a line-range edit whose edges cut a multi-line string is refused,
and the refusal names the string's true first and last lines.

On 2026-09-29 (p181-t447-coder) the model's range crossed a docstring boundary
(evidence line 786, an unterminated string) and all three attempts broke the
file the same way. apply_ops replaces whole lines, so a range that crosses a
string boundary must be refused with the string's span named. The retry that
covers the whole string -- or no string at all -- applies cleanly.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def edit(src, lo, hi, new):
    """Apply one line-range op to m.py in a fresh temp dir; -> (ok, why, before, after)."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "m.py"
        f.write_text(src, encoding="utf-8")
        before = f.read_text(encoding="utf-8")
        ok, why = coder.apply_ops(Path(d), [{"path": "m.py", "from": lo, "to": hi, "new": new}])
        return ok, why or "", before, f.read_text(encoding="utf-8")


def main():
    import ast

    # m.py: 1 x = 1 / 2 s = """start / 3 middle / 4 end""" / 5 y = 2
    # The t469 break: the range's lower edge falls inside the string; applied on
    # main it leaves the open quote on line 2 unclosed and the file unparseable.
    src = 'x = 1\ns = """start\nmiddle\nend"""\ny = 2\n'
    print("A range that cuts a multi-line string is refused, with the string's span named:")
    ok, why, before, after = edit(src, 3, 4, "MIDDLE")
    report("a range that crosses the string is refused", ok, False)
    report("  and the file is left untouched", after, before)
    report("  and the refusal says it is a string", "string" in why.lower(), True)
    report("  and names the string's first and last lines", "2" in why and "4" in why, True)

    # m.py: 1 s = """start / 2 middle / 3 end""" / 4 y = 2
    # The mirror: the range's upper edge falls inside the string; applied on main
    # the close quote on line 3 survives as an orphan and the file breaks again.
    src2 = 's = """start\nmiddle\nend"""\ny = 2\n'
    ok, why, before, after = edit(src2, 1, 2, 's = "flat"')
    report("a range that ends inside the string is refused too", ok, False)
    report("  and the file is left untouched", after, before)
    report("  and it names lines 1 and 3", "1" in why and "3" in why and "string" in why.lower(), True)

    print("A range that covers the whole string -- or no string at all -- still applies:")
    ok, why, before, after = edit(src, 2, 4, 's = "flat"')
    report("replacing a whole string applies", ok, True)
    report("  and rewrites exactly those lines", after, 'x = 1\ns = "flat"\ny = 2\n')
    report("  and the result parses", bool(ast.parse(after)), True)

    src3 = 'a = """one\ntwo"""\nb = 1\n'
    ok, why, before, after = edit(src3, 3, 3, "b = 2")
    report("a range after a complete string applies", ok, True)
    report("  and the string survives byte-for-byte", 'a = """one\ntwo"""' in after, True)

    # The shape this guard must not swallow (test_coder_guards.py's own fixture):
    src4 = "one\ntwo\nthree\nfour\n"
    ok, why, before, after = edit(src4, 2, 3, "TWO\nTHREE")
    report("a string-free file edits exactly as before", (ok, after), (True, "one\nTWO\nTHREE\nfour\n"))

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
