#!/usr/bin/env python3
"""A box on the Scripts tab: set one knob, read it back from the FILE, and refuse what must not be changed.

Jas, 2026-10-30: "I want boxes that I can manipulate."

Two guards a box needs and a terminal does not, both here:
  * it is not the Hub's own code -- every projects/*.py is IN the script library, so a crafted form could otherwise
    rewrite feedback.py, the page serving the form, while it runs;
  * the value shown afterwards is READ BACK from the file, not echoed from the request. A page that repeats what was
    typed is not evidence that anything was written.

Every case edits a COPY of a real script in a temporary workspace, so no live file can be changed by running this.
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "app-repo"))
import feedback          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


tmp = Path(tempfile.mkdtemp())
(tmp / "scripts").mkdir()
shutil.copy(REPO / "scripts" / "guarded_test.py", tmp / "scripts" / "guarded_test.py")
real_ws = feedback.WORKSPACE
feedback.WORKSPACE = tmp
try:
    ok, msg, now = feedback.apply_knob("scripts/guarded_test.py", "POLL_S", "9")
    check("a knob is set and the message carries the new value", ok and now == 9, msg)
    check("  and the FILE says 9, which is the read-back", "POLL_S = 9" in (tmp / "scripts" / "guarded_test.py")
          .read_text(encoding="utf-8"), "the file did not change")

    ok, msg, now = feedback.apply_knob("scripts/guarded_test.py", "NOPE", "1")
    check("an unknown knob is refused", ok is False and "no knob" in msg.lower(), msg)

    ok, msg, _ = feedback.apply_knob("scripts/guarded_test.py", "DEFAULT_NEED_GIB", "not-a-number")
    check("a wrong-typed value is refused", ok is False and "float" in msg.lower(), msg)

    ok, msg, _ = feedback.apply_knob("projects/app-repo/feedback.py", "X", "1")
    check("THE HUB'S OWN CODE is refused", ok is False and "not under scripts/" in msg, msg)

    ok, msg, _ = feedback.apply_knob("scripts/../projects/app-repo/feedback.py", "X", "1")
    check("  and so is a traversal that starts with scripts/", ok is False, msg)
finally:
    feedback.WORKSPACE = real_ws
    shutil.rmtree(tmp, ignore_errors=True)

src = (REPO / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
check("the page offers a form that posts to /scripts/set", 'action="/scripts/set"' in src)
check("  and the route exists", 'if path == "/scripts/set":' in src)
check("  and it says a restart is needed for a running script", "keeps the value it started with" in src)

print("test_knob_box:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
