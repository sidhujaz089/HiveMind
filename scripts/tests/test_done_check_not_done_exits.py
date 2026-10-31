#!/usr/bin/env python3
"""A done-check that verified NOT DONE leaves the Done? column (2026-10-28).

DS checked 19 done-checks: 3 were done, 16 were not. Only closing a task removed it from the
column, so the 16 stayed "waiting on Claude" after the verdict. A note carrying
task_board.NOT_DONE_MARK, written after the manager's answer, sends the task back to the queue.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import task_board as TB  # noqa: E402

FAILED = 0


def check(name, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


ANSWER = "x" * 200 + "\n(a) ALREADY DONE -- the ledger exists\n"
with tempfile.TemporaryDirectory() as td:
    repo = Path(td)
    (repo / "workspace" / "manager-answers").mkdir(parents=True)
    (repo / "workspace" / "tasks").mkdir(parents=True)
    for tid in ("t701", "t702", "t703"):
        (repo / "workspace" / "manager-answers" / f"{tid}.md").write_text(ANSWER[200:] + ANSWER[:200])
    old = time.time() - 3600
    for tid in ("t701", "t702", "t703"):
        os.utime(repo / "workspace" / "manager-answers" / f"{tid}.md", (old, old))
    (repo / "workspace" / "tasks" / "t701--a.md").write_text("---\nneeds: \n---\nbody\n")
    (repo / "workspace" / "tasks" / "t702--b.md").write_text(f"---\nneeds: \n---\nbody\n\n{TB.NOT_DONE_MARK} -- no RCA file\n")
    t3 = repo / "workspace" / "tasks" / "t703--c.md"
    t3.write_text(f"---\nneeds: \n---\nbody\n\n{TB.NOT_DONE_MARK} -- stale\n")
    os.utime(t3, (old - 60, old - 60))
    st = TB.manager_stages(repo)
    check("an unverified done-check stays in the column", st.get("t701", (None,))[0], "done-check")
    check("a verified NOT DONE leaves it", "t702" in st, False)
    check("a NOT DONE note OLDER than a newer answer does not", st.get("t703", (None,))[0], "done-check")

print("test_done_check_not_done_exits:", "ok" if not FAILED else "FAILED")
sys.exit(1 if FAILED else 0)
