#!/usr/bin/env python3
"""An owned pause whose owner has died is released; an unowned pause never is.

2026-10-12: session_end paused the line for its test sweep at 14:58Z, was killed mid-sweep (a
finally does not run on SIGKILL), and nothing dispatched for three hours. Fakes only: the flag
path is repointed into a tempdir.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import scheduler_commands as S  # noqa: E402

fails = []
with tempfile.TemporaryDirectory() as td:
    saved = S.PAUSE_FLAG
    S.PAUSE_FLAG = Path(td) / "paused.enabled"
    try:
        dead = subprocess.Popen([sys.executable, "-c", "pass"]); dead.wait()
        S.pause("session_end", "sweep", owner_pid=dead.pid)
        if S.is_paused():
            fails.append("a pause whose owner is dead still reads as paused")
        if S.PAUSE_FLAG.exists():
            fails.append("the stale owned flag was not removed")
        S.pause("session_end", "sweep", owner_pid=__import__("os").getpid())
        if not S.is_paused():
            fails.append("a pause whose owner is alive was released")
        S.pause("jas", "by hand")
        if not S.is_paused():
            fails.append("an unowned pause (a person, the memory guard) was released")
    finally:
        S.PAUSE_FLAG = saved
for f in fails:
    print("FAIL:", f)
print("test_pause_owner_dead:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
