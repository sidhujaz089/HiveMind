#!/usr/bin/env python3
"""t667: a brief that names a METHOD by its bare name gets that method shown.

p249, p259 and p261 (feedback.py builds) named `do_GET`, a method of the Handler class. sliced() matched only
`Handler.do_GET`, so the model was shown the file's first 60 lines and plan_detail, but not the method it had to
edit; it asked for the file, was served the same slice, asked again, and was refused "no room for more of it" five
times. 20 of 29 symbol-briefed files reported a name "NOT FOUND", most of them remarks like "(new)" and constants.
Hermetic: pure functions on a string."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  [{'ok' if cond else 'BAD'}]  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


SRC = '''import os
LIMIT = 3
LABELS: dict = {}


def plan_detail(tid):
    return tid


class Handler:
    def do_GET(self):
        path = self.path
        if path.startswith("/plan/t"):
            return 1
        return 0

    def do_POST(self):
        return 2

    def helper(self):
        return 3


class Other:
    def do_GET(self):
        return "other"
'''

cut = coder.sliced(SRC, ["do_GET", "plan_detail"])
ck("a bare method name is shown", 'if path.startswith("/plan/t"):' in cut)
ck("  and says nothing is missing", "NOT FOUND" not in cut, cut[-120:])
ck("  every class's method of that name is shown (the brief did not say which)", '"other"' in cut)
ck("  other methods stay out", "return 2" not in cut and "return 3" not in cut)

q = coder.sliced(SRC, ["Handler.do_GET"])
ck("a qualified name still picks only that class's method", 'return 1' in q and '"other"' not in q)

c = coder.sliced(SRC, ["LIMIT", "LABELS"])
ck("a module-level constant named on the line is shown", "LIMIT = 3" in c and "LABELS: dict" in c)
ck("  and a name that exists nowhere is still SAID missing",
   "NOT FOUND in this file: nope" in coder.sliced(SRC, ["nope"]))

ck("remarks on the SYMBOLS line are not symbols",
   coder.symbols_of("SYMBOLS: write_tick_row (new), tick, (coder.py:1351)\n") == ["write_tick_row", "tick"],
   str(coder.symbols_of("SYMBOLS: write_tick_row (new), tick, (coder.py:1351)\n")))
ck("a dotted name is kept", coder.symbols_of("SYMBOLS: Handler.do_GET plan_detail\n") == ["Handler.do_GET", "plan_detail"])

print("test_coder_bare_method_slice:", "FAILED" if FAILS else "ok")
sys.exit(1 if FAILS else 0)
