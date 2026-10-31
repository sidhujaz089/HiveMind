#!/usr/bin/env python3
"""Setting a script's number from a page: change the literal, nothing else, and refuse what is not a literal.

Jas, 2026-10-30: *"I want every script in there to be something I can tap on and adjust the variables. I want boxes that
I can manipulate."*

A box needs a writer, and the writer has to be narrower than sed: it edits the ONE literal `describe()` reported, leaves
comments and formatting alone, refuses a name bound to an expression (there is no safe way to "set" `X = compute()`),
and shows the diff before it writes. That is what makes a box safe enough to put in front of a person.

RED ON MAIN: scripts/set_knob.py does not exist.
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import set_knob as sk          # noqa: E402
import script_settings as ss   # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


SRC = '''#!/usr/bin/env python3
"""A script with knobs."""

# how many workers
MAX_WORKERS = 3
GREETING = "hi"
DERIVED = MAX_WORKERS * 2
'''
with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "s.py"
    p.write_text(SRC, encoding="utf-8")

    ok, msg, new = sk.set_knob(p, "MAX_WORKERS", "7", write=False)
    check("a dry run reports the change and does not write", ok and "MAX_WORKERS = 7" in new
          and p.read_text() == SRC, msg)

    ok, msg, new = sk.set_knob(p, "MAX_WORKERS", "7")
    after = p.read_text()
    check("a real run writes the literal", ok and "MAX_WORKERS = 7" in after, msg)
    check("  and leaves every other line alone",
          after.count("MAX_WORKERS") == 2 and 'GREETING = "hi"' in after and "DERIVED = MAX_WORKERS * 2" in after, after)
    check("  and the comment above it survives", "# how many workers" in after, after)
    check("  and the file still parses", ss.describe(p)["knobs"][0]["value"] == 7, str(ss.describe(p)["knobs"]))

    ok, msg, _ = sk.set_knob(p, "GREETING", "hello")
    check("a string knob takes a new string", ok and 'GREETING = "hello"' in p.read_text(), msg)

    ok, msg, _ = sk.set_knob(p, "DERIVED", "9", write=False)
    check("an expression is refused, not guessed at", ok is False and "expression" in msg.lower(), msg)

    ok, msg, _ = sk.set_knob(p, "NOT_A_KNOB", "1", write=False)
    check("an unknown name is refused", ok is False and "no knob" in msg.lower(), msg)

    ok, msg, _ = sk.set_knob(p, "MAX_WORKERS", "seven", write=False)
    check("a value of the wrong type is refused rather than coerced",
          ok is False and "int" in msg.lower(), msg)

print("test_set_knob:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
