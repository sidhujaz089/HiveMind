#!/usr/bin/env python3
"""A hand-written task file is refused; every legitimate write still lands.

WHY. A task file written by hand skips taskstore.py's lock and leaves QUEUE.md stale, so the
task exists on disk and is invisible in the plan -- including on the phone, which is where Jas
reads it. check_workspace.py catches it at PUSH time, after the body is composed and the work
done. This refuses it at the attempt. Jas, after it happened twice in one session: "Is there a
mechanical fixture that you can make to prevent hand writing tasks?"

THE SECOND HALF MATTERS MORE THAN THE FIRST. A guard that blocks too much gets disabled, and a
disabled guard is worse than none (AGENTS.md rule 9). So most of this suite is cases that must
NOT be blocked -- QUEUE.md, done/, every other file in the repository, and taskstore.py itself,
which writes these same files from a subprocess and therefore arrives as Bash rather than Write.
"""
import json
import os
import tempfile
import subprocess
import sys
from pathlib import Path

# THE TREE THIS SUITE IS IN, not a hardcoded checkout (t4385): with an absolute path, running
# this file from a worktree ran the LIVE checkout's taskstore, which is how the probe minted a
# real id into the live queue from a branch that had not merged yet.
REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "hooks" / "guard_task_writes.py"


def call(path, tool="Write"):
    payload = json.dumps({"tool_name": tool, "tool_input": {"file_path": path}})
    r = subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True,
                       text=True, timeout=30)
    return r.returncode, r.stderr


def cleanup_selftest_task(tasks_dir, filename, tid):
    """Remove the ONE probe file the self-test minted, and only its own QUEUE.md row.

    BY EXACT NAME, NEVER A PREFIX (t697, and the probes left on 2026-10-25). The inline cleanup this
    replaces globbed f"{tid}*.md", so a probe minted as t999 also deleted t9990--anything.md, which is
    somebody else's task, and it called unlink() on a path a concurrent taskstore add could have raced
    away -- the FileNotFoundError t697 records. Idempotent: a probe that is already gone is a no-op
    that reports nothing removed.

    Returns the paths actually removed, so a caller can say what it did rather than assume it.
    """
    tasks_dir = Path(tasks_dir)
    removed = []
    probe = tasks_dir / filename
    if probe.exists():
        probe.unlink()
        removed.append(probe)
    q = tasks_dir / "QUEUE.md"
    if q.exists():
        kept = []
        for line in q.read_text(encoding="utf-8").splitlines():
            toks = line.split()
            # The id is the SECOND token ("321. t999 -- ...") and it must match EXACTLY: a row for
            # t9990 shares the prefix but is a different task.
            if len(toks) >= 2 and toks[1] == tid:
                continue
            kept.append(line)
        q.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return removed


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'BAD'}]    {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(f"{label} {detail}")

    # 1. THE THING IT IS FOR.
    rc, err = call(str(REPO / "workspace/tasks/t999--written-by-hand.md"))
    check("a new task file written by hand is BLOCKED", rc == 2)
    check("and the refusal names the command to use instead",
          "taskstore.py add" in err)
    check("and says WHY, not just no",
          "invisible in the plan" in err.lower() or "INVISIBLE IN THE PLAN" in err)

    # 2. EDITING AN EXISTING TASK BY HAND IS THE SAME MISTAKE.
    existing = sorted((REPO / "workspace/tasks").glob("t2*.md"))
    if existing:
        rc, _ = call(str(existing[0]), tool="Edit")
        check("editing an existing task file by hand is BLOCKED too", rc == 2,
              existing[0].name)

    # 3. EVERYTHING BELOW MUST STILL WORK. A guard that blocks too much gets disabled.
    allowed = [
        (str(REPO / "workspace/tasks/QUEUE.md"), "QUEUE.md itself"),
        (str(REPO / "workspace/tasks/done/t001--anything.md"), "a finished task under done/"),
        (str(REPO / "projects/local-inference/settings.py"), "an ordinary source file"),
        (str(REPO / "workspace/HANDOVER-2026-09-04.md"), "a handover"),
        (str(REPO / "docs/LESSONS.md"), "a lesson"),
        (str(REPO / "workspace/tasks/notes.md"), "a non-task file in the tasks directory"),
    ]
    for path, why in allowed:
        rc, _ = call(path)
        check(f"NOT blocked: {why}", rc == 0)

    # 4. taskstore.py MUST KEEP WORKING. It writes these files from a subprocess, so it
    #    arrives as a Bash call and this hook never sees it. Proven end to end rather than
    #    reasoned about: create a task through the tool and confirm the file exists.
    #    THE PROBE MINTS INTO A THROWAWAY DIRECTORY (t4385). It used to add a real task in
    #    workspace/tasks, so every run of this file burned a task id and a crash left
    #    tNNNN--guard-self-test-delete-me.md in the live queue, which FAILS the plan check for the
    #    next person. HIVEMIND_TASKS_DIR points taskstore somewhere it can throw away. What this
    #    still proves is the thing that matters here: taskstore writes from a subprocess, so the
    #    hook never sees the Write.
    with tempfile.TemporaryDirectory(prefix="guard-selftest-tasks-") as probe_dir:
        r = subprocess.run([sys.executable, str(REPO / "projects/app-repo/taskstore.py"),
                            "add", "GUARD SELF TEST -- delete me", "local-inference", "test"],
                           capture_output=True, text=True, timeout=60, cwd=str(REPO),
                           env={**os.environ, "HIVEMIND_TASKS_DIR": probe_dir})
        tid = r.stdout.strip().splitlines()[-1].strip() if r.stdout.strip() else ""
        made = sorted(Path(probe_dir).glob(f"{tid}--*.md")) if tid else []
        check("taskstore.py can still create a task (it is not a Write, so the hook never fires)",
              bool(made), tid)
        check("  and the probe was minted OUTSIDE the live queue, so nothing is left for the plan "
              "check",
              not list((REPO / "workspace/tasks").glob(f"{tid}--*.md")) if tid else False, tid)
        if made:
            cleanup_selftest_task(Path(probe_dir), made[0].name, tid)

    # 5. A MALFORMED PAYLOAD MUST NOT BLOCK THE SESSION. A hook that crashes on unexpected
    #    input would break every Write in the repository.
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True,
                       text=True, timeout=30)
    check("garbage on stdin does not block anything", r.returncode == 0)

    for x in fails:
        print("FAIL:", x)
    print("test_task_write_guard:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
