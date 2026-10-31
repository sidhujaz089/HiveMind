#!/usr/bin/env python3
"""record_sweep.py MSG_FILE -- refuse a code commit that sweeps in run records (t452).

`git commit -a` stages every tracked change, so a one-file fix rode out with 45, 100 and 129
workspace run records (ee88933, a952cb7, e306fe3). A preference did not hold, so this is a check.

Called from commit-msg, NOT pre-commit: the two legitimate carriers are told apart by their
SUBJECT (`session:` closeout, `chore(workspace)` batch), and pre-commit never sees the message.
Result-based: by the time any hook runs the -a flag is gone; only the staged set is visible.
"""
import subprocess
import sys

RECORD_CAP = 2
RECORD_DIRS = ("workspace/run-logs/", "workspace/manager-answers/", "workspace/task-sessions/",
               "workspace/session-reviews/", "workspace/tasks/")
CARRIERS = ("session:", "session(", "chore(workspace)")


def is_record(path):
    return path.startswith("workspace/") and (path.endswith(".jsonl") or path.startswith(RECORD_DIRS))


def is_code(path):
    return path.startswith(("scripts/", "projects/"))


def main(msg_file):
    lines = [l for l in open(msg_file, encoding="utf-8", errors="replace").read().splitlines()
             if l.strip() and not l.startswith("#")]
    subject = lines[0] if lines else ""
    if subject.startswith(CARRIERS):
        return 0
    staged = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True,
                            text=True, timeout=60).stdout.split()
    records = [p for p in staged if is_record(p)]
    if not any(is_code(p) for p in staged) or len(records) <= RECORD_CAP:
        return 0
    print(f"""
  COMMIT REFUSED -- a code commit sweeps in {len(records)} workspace run records (cap {RECORD_CAP}).
  This is what `git commit -a` does (t452). Unstage them and commit the code alone:

    git restore --staged {' '.join(records[:5])}{' ...' if len(records) > 5 else ''}

  Records travel in a `chore(workspace): ...` or `session: ...` commit. Your message is preserved.
""")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
