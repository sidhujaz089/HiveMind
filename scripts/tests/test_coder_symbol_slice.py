#!/usr/bin/env python3
"""A brief that names its SYMBOLS gets only those shown; a prompt that cannot fit is refused first.

p20, 2026-09-23: coder.py (124 KB) was pasted whole -- a 52,538-token prompt in a 65,536-token
window -- for a change to one function, and two attempts ran out of room. The scope-schema
fix is a `SYMBOLS:` line in the brief; the harness fix is to refuse, before any attempt, a
prompt that cannot share the window with an answer. Pinned here:

  * symbols_of() reads the SYMBOLS: line, comma or space separated, Class.method allowed
  * sliced() shows the header and the named symbols with the file's REAL line numbers,
    keeps decorators, finds methods, says what it could not find, and returns None on a
    file that does not parse (so the caller falls back to the whole-file excerpt)
  * prompt_fits() refuses p20's numbers and passes an ordinary brief
  * main() slices when symbols are named and refuses NEEDS-SCOPE before the first attempt
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


SRC = '''import os
LIMIT = 3


def keep():
    return 1


@staticmethod
def target(x):
    return x + LIMIT


class Box:
    def other(self):
        return 0

    def put(self, v):
        return v
'''


def main():
    print("symbols_of reads the brief's SYMBOLS: line:")
    report("comma and space separated",
           coder.symbols_of("TASK: t1\nSYMBOLS: target, Box.put  keep\nDo it."),
           ["target", "Box.put", "keep"])
    report("none named -> empty", coder.symbols_of("TASK: t1\nno symbols here"), [])

    print("\nsliced shows only what was named, at the real line numbers:")
    out = coder.sliced(SRC, ["target", "Box.put", "missing_fn"])
    lines = SRC.splitlines()
    tl = next(i for i, l in enumerate(lines, 1) if l.startswith("def target"))
    report("the named function is shown at its own line number",
           f"{tl:5d}  def target(x):" in out, True)
    report("  with its decorator", f"{tl - 1:5d}  @staticmethod" in out, True)
    report("a Class.method is found", "def put(self, v):" in out, True)
    report("an unnamed function is NOT shown", "def keep" in out, False)
    report("  nor an unnamed method", "def other" in out, False)
    report("the header (imports, constants) is shown", "LIMIT = 3" in out, True)
    report("an unfound name is said, not dropped", "NOT FOUND in this file: missing_fn" in out, True)
    report("a file that does not parse returns None", coder.sliced("def (:\n", ["x"]), None)

    print("\nprompt_fits refuses what cannot share the window with an answer:")
    report("p20: ~52.5k-token prompt + 16000 in a 65536 window does not fit",
           coder.prompt_fits(52538 * 3, 16000, 65536)[0], False)
    report("an ordinary 20k-token prompt fits", coder.prompt_fits(20000 * 3, 16000, 65536)[0], True)
    report("an unknown window is not a refusal", coder.prompt_fits(10 ** 7, 16000, None)[0], True)

    print("\nmain() uses both:")
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    report("the starting context slices when the brief names symbols",
           "cut = sliced(text, symbols)" in src, True)
    gate = src.find("record(a.task, a.model, \"NEEDS-SCOPE\"")
    loop = src.find("for n in range(1, a.attempts + a.reads + 1):")
    report("NEEDS-SCOPE is recorded before the first attempt", 0 < gate < loop, True)

    ask_at = src.find("body = excerpt(rel)")
    report("a REQUESTED file goes through excerpt() too (p50 asked for coder.py and got it whole)",
           ask_at > loop, True)
    report("  and is refused by name when it cannot fit beside the prompt",
           "too large to show beside the prompt" in src[ask_at:ask_at + 1200], True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
