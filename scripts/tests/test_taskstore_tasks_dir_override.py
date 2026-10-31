#!/usr/bin/env python3
"""t4385: taskstore can be pointed at a throwaway tasks directory, so a self-test mints no real id.

The write-guard self-test adds a real task to prove taskstore.py still works, so every run of that
file burned a task id -- and a crashed or mis-cleaned run left t4218--guard-self-test-delete-me.md and
t4385--guard-self-test-delete-me.md in workspace/tasks. Those FAIL "QUEUE.md and the task files name
the same set" for whoever runs the check next, which happened twice on 2026-10-25.

HIVEMIND_TASKS_DIR makes the mint land somewhere disposable. The second assertion is the one that
matters: a test that only proved the file appeared in the temp dir would still pass if the task had
ALSO been created in the live queue, which is the failure being fixed.

Hermetic: one subprocess, with the env var set, writing only under a TemporaryDirectory. The live
tasks directory is read (a listing) and never written.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TASKSTORE = ROOT / "projects" / "app-repo" / "taskstore.py"
LIVE_TASKS = ROOT / "workspace" / "tasks"

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    before = {p.name for p in LIVE_TASKS.glob("t*.md")}
    with tempfile.TemporaryDirectory(prefix="tasks-dir-override-") as d:
        r = subprocess.run([sys.executable, str(TASKSTORE), "add", "OVERRIDE PROBE -- delete me",
                            "local-inference", "test"],
                           capture_output=True, text=True, timeout=60, cwd=str(ROOT),
                           env={**os.environ, "HIVEMIND_TASKS_DIR": d})
        tid = r.stdout.strip().splitlines()[-1].strip() if r.stdout.strip() else ""
        assert tid.startswith("t"), f"taskstore printed no id: {r.stdout!r} {r.stderr!r}"
        made = sorted(Path(d).glob(f"{tid}--*.md"))
        check("the override makes taskstore write the task into the given directory",
              len(made) == 1, (tid, [p.name for p in Path(d).iterdir()]))
        check("  it writes a QUEUE.md there too, so the id comes from that directory",
              (Path(d) / "QUEUE.md").exists(), list(Path(d).iterdir()))
        after = {p.name for p in LIVE_TASKS.glob("t*.md")}
        check("  and the LIVE queue gained nothing", after == before, sorted(after - before))

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
