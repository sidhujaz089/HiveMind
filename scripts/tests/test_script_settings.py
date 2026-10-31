#!/usr/bin/env python3
"""Every script in the library, described from ITS OWN COMMENTS, and its knobs found rather than listed (Jas, 2026-10-30).

Jas: *"I have an idea we have a script library. I want every script in there to be something I can tap on and adjust the
variables. I want boxes that I can manipulate and I want a How this page works that derives the functions of each
script from the comments."*

Two halves, and this is the one a page can be built on without the page:
  * WHAT IT DOES -- the module docstring, and for each function the docstring line (or the comment above it), which is
    "derives the functions from the comments" done mechanically instead of written by hand a second time;
  * WHAT YOU CAN TURN -- module-level constants that hold a LITERAL, with their current value, type and line. A name
    bound to an expression is not a knob: a page that offered it would be offering an edit it cannot make safely.

RED ON MAIN: scripts/script_settings.py does not exist.
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import script_settings as ss          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


FIXTURE = '''#!/usr/bin/env python3
"""Turn widgets into gadgets. One line above the fold."""

# THE GADGET LIMIT (2026-10-30): why this number exists.
GADGET_LIMIT = 12
NAME = "gadgets"
DEEPLY_ODD = (1, 2, 3)

derived = compute()


def make_widget(n):
    """Build one widget from n."""
    return n


# THE SECOND FUNCTION, described by the comment above it.
def ship(tmp):
    return tmp


class Gadget:
    """A gadget."""
'''

with tempfile.TemporaryDirectory() as td:
    p = Path(td) / "widget.py"
    p.write_text(FIXTURE, encoding="utf-8")
    d = ss.describe(p)

check("the purpose comes from the module docstring", d["purpose"].startswith("Turn widgets into gadgets"), d["purpose"])
fns = {f["name"]: f for f in d["functions"]}
check("functions are found", "make_widget" in fns and "ship" in fns, str(list(fns)))
check("  a docstring line is the summary", fns["make_widget"]["summary"] == "Build one widget from n.",
      fns["make_widget"]["summary"])
check("  else the comment above it is", "SECOND FUNCTION" in (fns["ship"]["summary"] or "").upper(),
      fns["ship"]["summary"])
check("  and classes are described too", "Gadget" in fns, str(list(fns)))
check("every described thing carries its line", all(f.get("line") for f in d["functions"]), str(d["functions"]))

knobs = {k["name"]: k for k in d["knobs"]}
check("a literal constant is a knob", "GADGET_LIMIT" in knobs and knobs["GADGET_LIMIT"]["value"] == 12,
      str(list(knobs)))
check("  with its type and line", knobs["GADGET_LIMIT"]["type"] == "int" and knobs["GADGET_LIMIT"]["line"] > 0,
      str(knobs["GADGET_LIMIT"]))
check("  a string constant too", knobs.get("NAME", {}).get("value") == "gadgets", str(knobs.get("NAME")))
check("  and a tuple", knobs.get("DEEPLY_ODD", {}).get("value") == [1, 2, 3], str(knobs.get("DEEPLY_ODD")))
check("a name bound to an EXPRESSION is not a knob", "derived" not in knobs, str(list(knobs)))
check("a function name is not a knob", "make_widget" not in knobs, str(list(knobs)))

# the library view: every registered script, so a page can list them
lib = ss.library()
check("the library lists registered scripts", isinstance(lib, list) and len(lib) > 100, str(len(lib)))
check("  each entry names its path", lib and all(set(e) >= {"path", "what"} for e in lib[:5]), str(lib[:1]))
check("a script that does not exist is an empty description, not a crash",
      ss.describe("/tmp/definitely-not-here.py")["purpose"] == "", "")

# --- SHELL SCRIPTS ARE IN THE LIBRARY TOO, so they must be describable too -------------------------------
SH = """#!/bin/bash
# Work the top of the queue, one task at a time.
# The second line of the block is still the purpose.

MAX_WORKERS=3
GREETING="hello"
DERIVED=$((MAX_WORKERS + 1))

# THE LOOP BODY: what a reader needs first.
run_task() {
  timeout 5700 python3 scripts/ask_q16.py --prompt x --max-rounds 12 --timeout 5400
}
"""
with tempfile.TemporaryDirectory() as td:
    q = Path(td) / "loop.sh"
    q.write_text(SH, encoding="utf-8")
    d = ss.describe(q)

check("a shell script has a purpose from its leading comment block",
      d["purpose"].lower().startswith("work the top of the queue"), d["purpose"])
sh_knobs = {k["name"]: k for k in d["knobs"]}
check("  a literal assignment is a knob", sh_knobs.get("MAX_WORKERS", {}).get("value") in (3, "3"),
      str(sh_knobs.get("MAX_WORKERS")))
check("  a quoted string is a knob", str(sh_knobs.get("GREETING", {}).get("value")) == "hello", str(sh_knobs.get("GREETING")))
check("  an assignment built from another variable is NOT", "DERIVED" not in sh_knobs, str(list(sh_knobs)))
sh_fns = {f["name"]: f for f in d["functions"]}
check("  a shell function is described", "run_task" in sh_fns, str(list(sh_fns)))
check("    from the comment above it", "LOOP BODY" in (sh_fns.get("run_task", {}).get("summary") or "").upper(),
      str(sh_fns.get("run_task", {}).get("summary")))
flags = {(f["flag"], f["value"]) for f in d.get("flags") or []}
check("  the numeric flags it passes are found", ("--max-rounds", "12") in flags and ("--timeout", "5400") in flags,
      str(sorted(flags)))

print("test_script_settings:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
