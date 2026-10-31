#!/usr/bin/env python3
"""A brief that names its LINES gets only those shown -- the line-number half of window-full.

p54 / t427, 2026-09-26: a brief named lines 719, 722, 1854, but coder.py only slices when the
brief names SYMBOLS (functions). With no function named, excerpt() returned the whole file --
a 61,900-token prompt in a 65,536-token window -- and prompt_fits could only REFUSE, it could
not make room. The window-full entry in failure-schema.json therefore needs a line-based
fallback, not a bigger output budget. Pinned here:

  * lines_of() reads line numbers from the brief (LINES: line and 'lines 719, 722' prose)
  * sliced_by_lines() shows a window around each named line at the file's REAL line numbers,
    dedupes overlapping windows, and returns None when nothing is named or the file is empty
  * main() falls back to line scoping when no SYMBOLS are named but lines are
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


# 20 source lines; the two targets sit far apart so their pad-windows do not overlap.
SRC = "\n".join(f"# line {i}" for i in range(1, 21))


def main():
    print("lines_of reads the brief's line numbers:")
    report("a LINES: line, comma separated",
           coder.lines_of("TASK: t427\nLINES: 719, 722, 1854\nDo it."),
           [719, 722, 1854])
    report("prose 'lines 719, 722'",
           coder.lines_of("Fix the bug at lines 719, 722 of the file."),
           [719, 722])
    report("singles and duplicates collapse, order kept",
           coder.lines_of("LINES: 722, 719, 722"),
           [722, 719])
    report("none named -> empty", coder.lines_of("TASK: t427\nno numbers here"), [])

    print("\nsliced_by_lines shows a window around each named line, at real line numbers:")
    out = coder.sliced_by_lines(SRC, [3, 18], pad=1)
    report("a named line is shown at its own number", f"{3:5d}  # line 3" in out, True)
    report("  with its pad neighbour", f"{4:5d}  # line 4" in out, True)
    report("a distant named line is shown too", f"{18:5d}  # line 18" in out, True)
    report("an unnamed middle line is NOT shown", f"{10:5d}  # line 10" in out, False)
    report("overlapping windows dedupe", out.count("# line 3") == 1, True)
    report("nothing named returns None", coder.sliced_by_lines(SRC, []), None)
    report("an empty file returns None", coder.sliced_by_lines("", [3]), None)

    print("\nmain() uses the fallback when no SYMBOLS are named but lines are:")
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    report("the starting context slices by lines when symbols are empty",
           "sliced_by_lines(text, lines)" in src, True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())