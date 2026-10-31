#!/usr/bin/env python3
"""Every reassignment leaves a note naming the code that made it and why (Jas, 2026-10-19).

One migration moved 166 open tasks to Jas and ~300 went to Claude across session commits, none with
a note. set_meta now writes the note itself whenever the assignee changes, so no caller can skip it."""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
import taskstore as ts  # noqa: E402

failed = 0


def check(name, ok):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


d = Path(tempfile.mkdtemp(dir=str(Path.home() / ".cache" / "hivemind-tmp")))
saved = (ts.TASKS, ts.QUEUE, ts.LOCK)
try:
    ts.TASKS, ts.QUEUE, ts.LOCK = d / "tasks", d / "tasks" / "QUEUE.md", d / "tasks" / ".lock"
    tid = ts.add("A test task", area="workspace", body="body", source="test")
    body = lambda: ts.find(tid)[1]  # noqa: E731
    before = body().count("Reassigned")
    ts.set_meta(tid, needs="claude")
    check("a reassignment with no reason still leaves a note", body().count("Reassigned") == before + 1)
    check("  naming old and new", "production -> technical" in body()) # needs: is a tier (2026-10-29)
    check("  naming the calling code, not taskstore", "test_no_silent_reassignment.py" in body())
    check("  and saying no reason was given", "no reason given" in body())
    ts.set_meta(tid, needs="jas", why="needs his call on the colour")
    check("a reason given is recorded", "needs his call on the colour" in body())
    n = body().count("Reassigned")
    ts.set_meta(tid, needs="jas", why="same again")
    check("no change, no note", body().count("Reassigned") == n)
    ts.set_meta(tid, area="flow")
    check("other fields leave no reassignment note", body().count("Reassigned") == n)
finally:
    ts.TASKS, ts.QUEUE, ts.LOCK = saved
    shutil.rmtree(d, ignore_errors=True)
print("FAILED" if failed else "ok")
sys.exit(1 if failed else 0)
