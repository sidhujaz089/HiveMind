#!/usr/bin/env python3
"""The console's read_file numbers every line and lets the model read on past a cut.

2026-09-24: Q4's cited line numbers were guesses because read_file returned bare text, and files
were cut at 24,000 characters with no way to reach the rest.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import console  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    text = "\n".join(f"line {i}" for i in range(1, 301))
    out = console.numbered_read(text)
    report("every line carries its real number", out.splitlines()[219].strip(), "220  line 220")
    cut = console.numbered_read(text, limit=500)
    report("a cut says which line to start from", "call read_file with start=" in cut, True)
    nxt = int(cut.split("start=")[1].split(" ")[0])
    report("  and reading on from there continues exactly", console.numbered_read(
        text, start=nxt, limit=100000).splitlines()[0].strip(), f"{nxt}  line {nxt}")
    report("start past the end is said", "past the end" in console.numbered_read(text, start=999), True)
    real = console.tool_read({"path": "scripts/memory.py", "start": 220})
    report("tool_read serves numbered lines from the file", real.splitlines()[0].split()[0], "220")
    report("the schema offers `start`",
           "start" in console.TOOLS[0]["function"]["parameters"]["properties"], True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
