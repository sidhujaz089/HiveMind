#!/usr/bin/env python3
"""A task cannot be closed while the failure ceiling still names it as a red suite's owner.

2026-10-12: t1056 was marked done while test-failures-ceiling.json allowed
test_dispatch_intake_counts.py to fail on its name. test_ceiling_owned went red, red on main
held every build, and 129 of 175 chunks in auto-assigned.json were 'HELD: red on main'.
Fakes only: a temp ceiling; add_note is captured, so no task file is touched.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "app-repo"))
import taskstore as T  # noqa: E402

fails = []
with tempfile.TemporaryDirectory() as td:
    c = Path(td) / "ceiling.json"
    c.write_text(json.dumps({"allowed_to_fail": ["a.py", "b.py"],
                             "_r": {"a.py": "t9001 (x): red for a reason", "b.py": "t90011: other"}}))
    saved, notes = (T.CEILING, T.add_note), []
    T.CEILING, T.add_note = c, (lambda tid, text, who=None, **k: notes.append((tid, text)))
    try:
        if T.owned_red_suites("t9001") != ["a.py"]:
            fails.append(f"owner match is not exact: {T.owned_red_suites('t9001')}")
        for st in ("done", "cancelled"):
            if T.set_status("t9001", st) is not False:
                fails.append(f"set_status({st}) did not refuse a task owning a red suite")
        if not notes or "a.py" not in notes[0][1]:
            fails.append(f"the refusal left no note naming the suite: {notes}")
        if T.owned_red_suites("t9002") != []:
            fails.append("a task owning nothing is reported as an owner")
    finally:
        T.CEILING, T.add_note = saved
for f in fails:
    print("FAIL:", f)
print("test_close_refuses_owned_red:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
