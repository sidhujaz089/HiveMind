#!/usr/bin/env python3
"""When the brief names SYMBOLS, the harness must never send the whole file:
if the slice did not stand, it refuses before the first attempt and says why.

t447 (2026-09-27, q16-supervisor): brief-p59-t427-coder.txt named
SYMBOLS: symbols_of, sliced, prompt_fits, yet the prompt was 63625 of 65536
tokens -- the whole of coder.py -- and four attempts burned the window.
test_coder_symbol_slice.py (read in full) proves sliced() itself works on a
parseable file with real symbols, so the defect is the decision path: the
fallback quietly sent the whole file.  Pinned here:

  * scope_gate(brief, text, chosen) -> (ok, reason):
      - no SYMBOLS named                  -> ok (no expectation on the context)
      - SYMBOLS named, `chosen` holds no
        function that was not named       -> ok (a proper slice, or a small file)
      - SYMBOLS named, `chosen` holds an
        unnamed function (whole file)     -> not ok, and the reason names the
                                             whole file, not just "window full"
  * main() consults the gate BEFORE the attempt loop and, on refusal, records
    NEEDS-SCOPE carrying the reason -- the same ordering the pinned test
    already enforces for NEEDS-SCOPE vs the loop.
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
    print("  %s  %s%s" % ("PASS" if ok else "FAIL", name,
                          "" if ok else "   (got %r, want %r)" % (got, want)))


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

BRIEF_NAMED = "TASK: t447\nSYMBOLS: target, Box.put\nDo it."
BRIEF_BARE = "TASK: t447\nno symbols here"


def main():
    print("scope_gate: SYMBOLS named, whole file chosen -> refuse, name the cause:")
    ok, reason = coder.scope_gate(BRIEF_NAMED, SRC, SRC)
    report("the whole-file fallback is refused", ok, False)
    report("and the reason names the whole file",
           ("whole file" in reason.lower()) or ("fallback" in reason.lower()), True)

    print("\nscope_gate: SYMBOLS named, the slice chosen -> pass:")
    cut = coder.sliced(SRC, ["target", "Box.put"])
    ok, reason = coder.scope_gate(BRIEF_NAMED, SRC, cut)
    report("a proper slice is allowed", ok, True)

    print("\nscope_gate: no SYMBOLS named -> no expectation:")
    ok, reason = coder.scope_gate(BRIEF_BARE, SRC, SRC)
    report("a bare brief may carry the whole file", ok, True)

    print("\nmain() enforces the gate before any attempt:")
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    call = src.find("ok, scope_reason = scope_gate(")
    loop = src.find("for n in range(1, a.attempts + a.reads + 1):")
    report("the gate is consulted", call > 0, True)
    report("before the attempt loop", 0 < call < loop, True)
    region = src[call:loop] if (0 < call < loop) else ""
    report("the refusal records NEEDS-SCOPE with the reason",
           ("NEEDS-SCOPE" in region) and ("scope_reason" in region), True)

    print("\n%d passed, %d failed" % (PASSED, FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
