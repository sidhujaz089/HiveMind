#!/usr/bin/env python3
"""A blocked task re-routed to production after the manager's answer leaves the column (2026-10-29).

Claude moved t774 and t697 from BLOCKED to production with a reason; the Review tab still listed both
as waiting on Claude, because only a newer manager answer moved the board stage.
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


ANSWER = "(d) BLOCKED -- by another task\n" + "x" * 220 + "\n"
REROUTE = "**2026-10-29 · claude** — Reassigned technical -> production by x: re-scope it\n"
OTHER = "**2026-10-29 · claude** — a plain note, not a reassignment\n"
with tempfile.TemporaryDirectory() as td:
    repo = Path(td)
    (repo / "workspace" / "manager-answers").mkdir(parents=True)
    (repo / "workspace" / "tasks").mkdir(parents=True)
    old = time.time() - 3600
    for tid in ("t701", "t702", "t703"):
        a = repo / "workspace" / "manager-answers" / f"{tid}.md"
        a.write_text(ANSWER)
        os.utime(a, (old, old))
    (repo / "workspace" / "tasks" / "t701--a.md").write_text("---\nneeds: production\n---\nbody\n\n" + REROUTE)
    t2 = repo / "workspace" / "tasks" / "t702--b.md"
    t2.write_text("---\nneeds: production\n---\nbody\n\n" + REROUTE)
    os.utime(t2, (old - 60, old - 60))
    (repo / "workspace" / "tasks" / "t703--c.md").write_text("---\nneeds: production\n---\nbody\n\n" + REROUTE + OTHER)
    st = TB.manager_stages(repo)
    check("re-routed to production AFTER the answer: leaves Blocked", "t701" in st, False)
    check("re-routed BEFORE the answer: stays", st.get("t702", (None,))[0], "blocked")
    check("a later plain note is not a re-route: stays", st.get("t703", (None,))[0], "blocked")
print("test_blocked_rerouted_exits:", "ok" if not FAILED else "FAILED")
sys.exit(1 if FAILED else 0)
