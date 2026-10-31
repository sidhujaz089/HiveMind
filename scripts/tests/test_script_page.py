#!/usr/bin/env python3
"""The Scripts tab explains a script from its own comments (Jas, 2026-10-30: "a How this page works that derives the
functions of each script from the comments").

The page renders script_settings.describe(), so the explanation cannot drift from the file. What is asserted here is the
JOIN -- that the Hub's function actually reaches the describer and puts the purpose, a knob and a function name on the
page, for a Python script and for a shell one, because the scoping loop is a shell script and the first version of the
describer answered "empty" for those.

READ-ONLY: the write path (set_knob.py) exists, but wiring a box to it needs a restart-confirm and a read-back, and
this page does not pretend to have them. That is asserted too, so a later change cannot quietly add a write.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "app-repo"))
import feedback          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


py = feedback.script_detail_html("scripts/guarded_test.py")
check("a Python script is explained", "guarded_test.py" in py, "no name")
check("  its purpose comes from its docstring", "Run ONE test command" in py, "no purpose")
check("  a knob is shown with its value", "DEFAULT_NEED_GIB" in py and "12.0" in py, "no knob")
check("  a function is shown", "wait_for" in py, "no function")
check("  the knob's own comment explains it", "the box needs to itself" in py or "must have room" in py, "no comment")

sh = feedback.script_detail_html("scripts/scoping_queue.sh")
check("a SHELL script is explained too, because the scoping loop is one", "TOP of QUEUE.md" in sh, sh[:120])
check("  with the numbers it runs with", "--max-rounds" in sh and "12" in sh, "no flags")
check("  and its shell functions", "run_task" in sh, "no functions")

bad = feedback.script_detail_html("scripts/does-not-exist.py")
check("a path that does not exist says so instead of crashing", "no description" in bad or "empty" in bad, bad[:120])

src = (REPO / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
i = src.index("def script_detail_html")
j = src.index("\ndef ", i + 10)          # the NEXT function, whatever it is called -- apply_knob sits between these now
body = src[i:j]
# THE DOCSTRING MAY NAME set_knob.py -- it explains why the write is NOT here. What must be absent is a CALL or a write.
check("the page is READ-ONLY -- no write path is wired here",
      "set_knob(" not in body and "write_text" not in body and ".save(" not in body,
      "a write appeared in the read-only view")

print("test_script_page:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
