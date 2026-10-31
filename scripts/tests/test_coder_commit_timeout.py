#!/usr/bin/env python3
"""A COMMIT THAT TIMES OUT IS AN OUTCOME, NOT A CRASH (addition (b), 2026-10-30).

The brief listed this as "an uncaught git-commit timeout in coder.py:write_branch (alleged for t1331/t1349). I could
not confirm it -- start from those tasks' logs." CONFIRMED from the log itself:

    subprocess.TimeoutExpired: Command '['git', '-C', '/home/user/.cache/hivemind-tmp/review-brief-40e73eb0',
      '-c', 'core.hooksPath=scripts/hooks', 'commit', '-F', '-']' timed out after 300 seconds
      recorded this failure on t1331 and moved it to the front of QUEUE.md

The traceback runs from coder.py's __main__ through write_branch to subprocess.communicate -- no handler anywhere.
So a commit that took longer than 300 s killed the whole run: the patch was applied in the worktree and never
committed, the worktree was left behind in ~/.cache/hivemind-tmp (which the reaper sweeps), and the failure was
recorded as `unclassified`, which tells the next reader nothing.

RED ON MAIN: coder has no _run_commit, so the timeout escapes as an exception.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "projects" / "local-inference"))
import coder          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


class Done:
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def boom(*a, **k):
    raise subprocess.TimeoutExpired(cmd="git commit", timeout=300)


rc, detail, timed_out = coder._run_commit("/tmp/wt-x", "msg", run=boom)
check("a timed-out commit does NOT raise", rc is None and timed_out is True, f"{rc}/{timed_out}")
check("  and it says it timed out", "timed out" in detail.lower(), detail)
check("  and it names the worktree holding the patch, so it is not lost",
      "/tmp/wt-x" in detail, detail)
check("  and it does not claim the commit happened", "not committed" in detail.lower(), detail)

rc, detail, timed_out = coder._run_commit("/tmp/wt-x", "msg", run=lambda *a, **k: Done(1, "hook said no", ""))
check("a REFUSED commit is still refused, unchanged", (rc, timed_out) == (1, False), f"{rc}/{timed_out}")
check("  and its output is carried whole", "hook said no" in detail, detail)

rc, detail, timed_out = coder._run_commit("/tmp/wt-x", "msg", run=lambda *a, **k: Done(0, "", ""))
check("a successful commit is unchanged", (rc, timed_out) == (0, False), f"{rc}/{timed_out}")

src = (HERE / "projects" / "local-inference" / "coder.py").read_text(encoding="utf-8")
check("write_branch calls it", "_run_commit(" in src and "timed_out" in src)
check("  and no bare subprocess.run(commit) is left to raise", src.count('"commit", "-F", "-"], input=msg') <= 1,
      "the one inside _run_commit")

print("test_coder_commit_timeout:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
