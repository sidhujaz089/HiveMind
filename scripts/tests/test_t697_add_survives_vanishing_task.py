#!/usr/bin/env python3
"""t697 oracle: a taskstore add survives a task file vanishing between glob and read.

On 2026-10-05 the task-guard self-test (which creates and deletes
tNNN--guard-self-test-delete-me.md) raced a live `taskstore.add` during the session_end
sweep: the add crashed with FileNotFoundError even though its own task file had been
written. The window is in load_all()/find(): task_files() globs the directory, then
parse() reads each path -- a file deleted in between raises FileNotFoundError and the
whole add dies on someone else's cleanup.

RED before the fix: load_all() (and so read_queue, write_queue, add) propagates
FileNotFoundError for a path that vanished after the glob. GREEN after: the vanished
file is skipped WITH A REPORT (never silently), every surviving task is still returned,
and an add in the same situation completes and files its task.

The race is reproduced deterministically: taskstore.task_files is patched to return the
real listing plus one path that no longer exists -- exactly the state after
glob-then-delete. No live service, no sleep-based racing, no touch of the live store
(HIVEMIND_TASKS_DIR points everything at a temp dir before import).
"""
import io
import os
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_td = tempfile.TemporaryDirectory()
os.environ["HIVEMIND_TASKS_DIR"] = str(Path(_td.name) / "tasks")
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))

import taskstore  # noqa: E402  (after the env override, so TASKS is the temp dir)

FAILS = []


def check(label, cond, detail=""):
    line = "  [%s] %s" % ("ok" if cond else "BAD", label)
    if not cond and detail:
        line += " -- " + detail
    print(line)
    if not cond:
        FAILS.append(label)


def main():
    print("t697 vanishing-task-file oracle (taskstore.load_all / find / add)\n")
    taskstore.TASKS.mkdir(parents=True, exist_ok=True)

    a = taskstore.add("first survivor", source="t697-oracle")
    b = taskstore.add("second survivor", source="t697-oracle")

    # The race, frozen: the listing contains a path that was deleted after the glob.
    ghost = taskstore.TASKS / "t692--guard-self-test-delete-me.md"
    real_task_files = taskstore.task_files

    def racy_task_files(include_done=False):
        return real_task_files(include_done) + [ghost]

    taskstore.task_files = racy_task_files
    try:
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                tasks = taskstore.load_all()
        except FileNotFoundError as e:
            check("load_all survives a task file deleted between glob and read", False,
                  "FileNotFoundError: %s" % e)
            tasks = {}
        else:
            check("load_all survives a task file deleted between glob and read", True)
        check("  ...the surviving tasks are all still returned",
              a in tasks and b in tasks, "got %r" % sorted(tasks))
        check("  ...the vanished id is not invented", "t692" not in tasks)
        check("  ...and the skip is REPORTED, not silent (the commit gate's rule)",
              "t692" in err.getvalue(), "stderr was %r" % err.getvalue())

        try:
            with redirect_stderr(io.StringIO()):
                meta, _body = taskstore.find(a)
            check("find() survives the same vanished file", (meta or {}).get("id") == a,
                  "got %r" % (meta,))
        except FileNotFoundError as e:
            check("find() survives the same vanished file", False,
                  "FileNotFoundError: %s" % e)

        # The observed crash: the ADD itself, racing the self-test's delete.
        try:
            with redirect_stderr(io.StringIO()):
                c = taskstore.add("the add that used to crash", source="t697-oracle")
        except FileNotFoundError as e:
            check("add() completes while another file vanishes mid-sweep", False,
                  "FileNotFoundError: %s" % e)
        else:
            check("add() completes while another file vanishes mid-sweep", True)
            check("  ...and its task file is on disk",
                  any(p.name.startswith(f"{c}--") for p in real_task_files()),
                  "no file for %s" % c)
            with redirect_stderr(io.StringIO()):
                check("  ...and the queue holds all three tasks",
                      set(taskstore.read_queue()) >= {a, b, c},
                      "queue: %r" % taskstore.read_queue())
    finally:
        taskstore.task_files = real_task_files

    n = len(FAILS)
    print("\n%d failed -- %s" % (n, "an add no longer dies on someone else's cleanup"
                                 if not n else "the glob-then-read window is still open"))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
