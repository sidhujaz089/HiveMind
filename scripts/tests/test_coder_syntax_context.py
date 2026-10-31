#!/usr/bin/env python3
"""t1425 -- coder.py's BAD-SYNTAX feedback must show the lines AROUND the error.

WHY. Dispatch p679-t1267-runledger (2026-10-18) produced the identical "unexpected indent" at
line 122 on attempts 5 AND 6 and the run died at its attempt budget. The feedback told the model
"the indentation must match the block you are replacing into" and showed it only the offending
line, rstrip'ed (py_syntax). A model cannot match indentation it has never been shown; attempt 3
of the same run passed syntax, so it can write correctly indented code.

RED ON MAIN, for the stated reason: coder.py defines no `syntax_context`, so the first report
fails and main() returns 1 before the rest runs.

Import idiom: insert projects/local-inference on sys.path, then import coder -- the idiom the
sibling coder suites use. Verified importable on this box.

Run:  python3 scripts/tests/test_coder_syntax_context.py
"""
import re
import shutil
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
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"  (got {got!r}, want {want!r})"))


# A nested body at FOUR distinct indentation depths (0, 4, 8, 12 spaces) so a build that strips,
# re-indents to one level, pads every line alike, or shows the offending line alone cannot
# satisfy the verbatim assertions below.
SRC = [
    "def outer(rows):",
    "    total = 0",
    "    for r in rows:",
    "        if r:",
    "            total += r",
    "            shown = r",
    "            bad = total",
    "            total += 1",
    "            yield total",
    "    return total",
]
LINE_RE = re.compile(r"^(..)\s+(\d+)\s{2}(.*)$")


def rows_of(out):
    """{line number: (marker, verbatim source)} from what the function returned."""
    got = {}
    for line in str(out).splitlines():
        m = LINE_RE.match(line)
        if m:
            got[int(m.group(2))] = (m.group(1), m.group(3))
    return got


def main():
    report("coder.py defines syntax_context", hasattr(coder, "syntax_context"), True)
    if not hasattr(coder, "syntax_context"):
        print(f"\n{PASSED} passed, {FAILED} failed")
        print("RED for the stated reason: the function t1425 asks for is not in coder.py")
        return 1
    fn = coder.syntax_context

    tmp = Path(tempfile.mkdtemp(prefix="t1425-"))
    try:
        (tmp / "target.py").write_text("\n".join(SRC) + "\n", encoding="utf-8")

        print("\nit shows the lines AROUND the error, at their real line numbers:")
        out = fn(tmp, "target.py", 7)
        report("an in-range line returns text", isinstance(out, str) and out.strip() != "", True)
        rows = rows_of(out)
        report("every line is numbered -- the enclosing def, the for, the if and the body",
               sorted(rows), list(range(1, 11)))
        report("the numbers are consecutive (no elided middle)",
               sorted(rows), list(range(min(rows), max(rows) + 1)))
        for n in range(1, 11):
            report(f"  line {n} is verbatim, leading whitespace intact",
                   rows.get(n, ("", None))[1], SRC[n - 1])
        report("the enclosing def shows ZERO indentation and the broken line shows TWELVE",
               [rows.get(1, ("", ""))[1][:1], rows.get(7, ("", ""))[1][:1]], ["d", " "])

        print("\nit marks the offending line, so the model knows which one to rewrite:")
        marks = sorted(n for n, (mk, _) in rows.items() if mk.strip())
        report("exactly one line is marked, and it is the error line", marks, [7])

        print("\nits default radius reaches the enclosing block (the note asks 118-126 at 122):")
        deep = ["def outer(rows):"] + [f"    step{i} = {i}" for i in range(1, 20)]
        (tmp / "deep.py").write_text("\n".join(deep) + "\n", encoding="utf-8")
        rows2 = rows_of(fn(tmp, "deep.py", 10))
        report("at line 10 of a 20-line file the window is lines 4..16 (radius 6 each way)",
               sorted(rows2), list(range(4, 17)))
        report("line 1 is OUTSIDE it -- a bounded window, not the whole file", 1 in rows2, False)
        report("line 20 is OUTSIDE it too", 20 in rows2, False)

        print("\nit degrades to no context rather than a crash (the retry must still go out):")
        first = rows_of(fn(tmp, "target.py", 1))
        report("an error at line 1 clamps at 1 -- no zero and no negative line numbers",
               [min(first), max(first)] if first else None, [1, 7])
        report("an out-of-range line number returns ''", fn(tmp, "target.py", 99), "")
        report("a file that is not there returns ''", fn(tmp, "nope.py", 3), "")
        report("lineno None (py_syntax can carry one) returns ''", fn(tmp, "target.py", None), "")
        report("a non-numeric line number returns ''", fn(tmp, "target.py", "x"), "")

        print("\nand the BAD-SYNTAX branch CALLS it (a function nothing calls is no fix):")
        src = (ROOT / "projects" / "local-inference" / "coder.py").read_text(encoding="utf-8")
        i = src.find('"outcome": "BAD-SYNTAX"')
        report("the BAD-SYNTAX branch is present in coder.py", i > 0, True)
        if i > 0:
            j = src.find("revert(root, baseline)", i)
            branch = src[i:j if j > 0 else len(src)]
            report("  the branch calls syntax_context(root, ...)", "syntax_context(root," in branch, True)
            report("  it keeps the existing range_block context block", "range_block(root," in branch, True)
            report("  coder.py as a whole keeps range_block (def + call)",
                   src.count("range_block(root,") >= 2, True)
            report("  it still tells the model the change was REVERTED", "REVERTED" in branch, True)
            report("  it no longer tells EVERY syntax error to fix the indentation (t469)",
                   "Fix the indentation and try again." in branch, False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
