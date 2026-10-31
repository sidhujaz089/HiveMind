#!/usr/bin/env python3
"""L2 Phase 0(a), 2026-10-27: a test must not move the live task id counter.

THE EVIDENCE. workspace/tasks/.ids read 9354 while the highest task file was t8860: 494 numbers
between them were minted and never became a task, and the handover that opened this effort put the
cumulative cost near 7,000 ids -- numbers a person may have seen on a task.

WHY IT HAPPENED. Two rules met in the wrong place. taskstore.id_space() mints from the LIVE sequence
when the checkout is not the live one (t1586, correct: a worktree's ids must not collide with
main's), and id_space()'s own docstring promised that "a fixture that repoints TASKS is its own
space" -- but the code only honoured that promise in the live checkout. So every taskstore suite run
from a worktree (where all four lanes work now) reserved a LIVE id and filed the task in a temp dir.
Three suites were doing it: test_taskstore_action_log, test_taskstore_file_confirms and
test_taskstore_wake, the first with a temp TASKS and no idea it was drawing real numbers.

THE FIX, two halves, and this suite is the oracle for both:
  1. id_space() honours the promise: a repointed TASKS is the fixture's own space in a worktree too.
  2. reserve_id() refuses when the space IS the real live store and the process looks like a test
     (live_state_guard: argv[0] under scripts/tests/ or test_*.py, or HIVEMIND_IN_TEST).

A REAL LINKED WORKTREE, not repointed module paths: the point is `git rev-parse --git-common-dir`
answering through a worktree, which is how the store decides. A temp pair of checkouts (one live, one
linked to it) reproduces that with nothing of the real store in reach. The control case proves the
half that must not change: a lane's own work in a worktree still takes a live number.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PASSED = FAILED = 0


def report(name, ok, detail=""):
    global PASSED, FAILED
    PASSED += bool(ok)
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        {detail}"))


CHILD = r'''
import json, os, sys
sys.path.insert(0, __APPREPO__)
import taskstore as T
from pathlib import Path
wt = Path(__WORKTREE__)
T.WORKSPACE = wt                     # the checkout is the worktree either way
if not os.environ.get("HIVEMIND_TASKS_DIR"):
    # A checkout in its DEFAULT layout: nothing repointed, so the id comes from the live sequence.
    t = wt / "workspace" / "tasks"
    (t / "done").mkdir(parents=True, exist_ok=True)
    T.TASKS, T.DONE = t, t / "done"
    T.QUEUE, T.LOCK = t / "QUEUE.md", t / ".lock"
    T.ATTACH = wt / "workspace" / "attachments"
    T.write_queue([])
else:
    Path(T.TASKS).mkdir(parents=True, exist_ok=True)   # the suite declared its own store
out = {"argv0": sys.argv[0]}
try:
    out["tid"] = T.add("a test task", source="test")
except Exception as e:
    out["error"] = type(e).__name__ + ": " + str(e)    # the FIX is on a later line; keep it all
print(json.dumps(out))
'''


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


def pair(tmp):
    """A live checkout and a LINKED WORKTREE of it, both empty, both real git trees."""
    live = Path(tmp) / "live"
    live.mkdir()
    (live / "workspace" / "tasks").mkdir(parents=True)
    (live / "workspace" / "tasks" / "QUEUE.md").write_text("", encoding="utf-8")
    git("init", "-q", "-b", "main", cwd=live)
    (live / "seed.txt").write_text("seed\n", encoding="utf-8")
    git("add", "-A", cwd=live)
    git("commit", "-q", "-m", "seed", cwd=live)
    wt = Path(tmp) / "worktree"
    git("worktree", "add", "-q", str(wt), "-b", "lane/topic", cwd=live)
    return live, wt


def mint(tmp, argv0, env=None, repoint_tasks=None):
    """Run add() in the LINKED worktree -- as a suite or as a lane, depending on argv[0]."""
    live, wt = pair(tmp)
    code = (CHILD.replace("__APPREPO__", repr(str(REPO / "projects" / "app-repo")))
                 .replace("__WORKTREE__", repr(str(wt))))
    holder = Path(tmp) / (Path(argv0).name if argv0 else "run.py")
    holder.parent.mkdir(parents=True, exist_ok=True)
    holder.write_text(code, encoding="utf-8")
    e = dict(os.environ, **(env or {}))
    if repoint_tasks:
        e["HIVEMIND_TASKS_DIR"] = str(repoint_tasks)
    p = subprocess.run([sys.executable, str(holder)], capture_output=True, text=True, env=e,
                       timeout=120, cwd=str(wt))
    try:
        out = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = {"error": "child produced no JSON", "stdout": p.stdout, "stderr": p.stderr}
    ledger = live / "workspace" / "tasks" / ".ids"
    out["live_ids"] = ledger.read_text(encoding="utf-8").strip() if ledger.exists() else None
    return out


def main():
    with tempfile.TemporaryDirectory() as tmp:
        out = mint(tmp, "scripts/tests/test_something.py")
        report("a TEST that files from a worktree never gets a live id", "tid" not in out, out)
        report("  and the live checkout's .ids was not written at all", out["live_ids"] is None, out)
        report("  and the refusal says what to do instead",
               "HIVEMIND_TASKS_DIR" in out.get("error", "") and "test" in out.get("error", "").lower(),
               out)

    with tempfile.TemporaryDirectory() as tmp:
        out = mint(tmp, "scripts/tests/test_something.py", env={"HIVEMIND_IN_TEST": "1"})
        report("the runner's HIVEMIND_IN_TEST marker refuses a suite whose argv[0] is not test-shaped",
               "tid" not in out and out["live_ids"] is None, out)

    with tempfile.TemporaryDirectory() as tmp:
        # THE CONTROL, and the half that matters: this is not "worktrees may not mint" -- it is
        # "TESTS may not". A lane filing real work from its worktree still takes a live number,
        # which is the whole point of t1586.
        out = mint(tmp, "scripts/lane_work.py")
        minted = out.get("tid", "")
        report("the same call from a NON-test process still mints from the live sequence",
               minted.startswith("t") and out["live_ids"] == str(int(minted[1:])), out)

    with tempfile.TemporaryDirectory() as tmp:
        # A suite that repoints TASKS is its own space (id_space's documented promise, which the
        # code did not keep in a worktree -- that WAS the leak).
        own = Path(tmp) / "fixture" / "tasks"
        out = mint(tmp, "scripts/tests/test_something.py", repoint_tasks=own)
        report("a suite that repoints TASKS mints in its own space and leaves the live .ids alone",
               out.get("tid", "").startswith("t") and out["live_ids"] is None, out)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
