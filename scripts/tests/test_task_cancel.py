#!/usr/bin/env python3
"""Fixtures for cancelling a task and for the RESOLVED.md view.

WHY THIS EXISTS. `taskstore.py cancel` shipped with `add_note(..., who="jas")`
hardcoded. The forgery guard (L-091) refuses a note attributed to Jas from any
caller that is not the feedback form, so `cancel` raised Forged for EVERY caller
and no task was ever cancelled -- 220 files on disk, zero with `status:
cancelled`, on a command that had been in the help text for a day. Nobody
noticed because the failure is invisible from the outside: the queue renders
perfectly, the status vocabulary contains "cancelled", and the only symptom is
that a thing Jas cancelled keeps appearing. He found it, not a check.

docs/CHECKS.md rule 1: a check is not trusted until it has been observed FAILING
on a fixture built to break it. So the first assertion here is the bug itself --
cancel invoked the way a person invokes it, from a shell, with no form in sight.
If that ever raises again, this fails.

The second half pins RESOLVED.md, and it pins BOTH sides on purpose. A view that
only ever lists cancelled items cannot be told apart from one that lists
everything; a view that only lists done items is the queue with extra steps. The
separation is the feature -- `done` feeds the keep-rate and the Pareto, and an
abandoned idea filed as done inflates both.
"""
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
STORE = ROOT / "projects" / "app-repo" / "taskstore.py"

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


TASK = ("---\nid: {tid}\ntitle: {tid} fixture\narea: workspace\nkind: build\n"
        "status: {status}\nneeds: claude\nsource: \ncreated: 2026-01-01T000000Z\n"
        "---\n\nbody\n\n## Notes\n\n## Attachments\n")


def sandbox(tmp, live=(), done=()):
    """A tasks/ tree shaped like the real one. taskstore.py locates it from its
    own path, so the copy has to sit where the real one sits."""
    app = tmp / "projects" / "app-repo"
    app.mkdir(parents=True)
    shutil.copy(STORE, app / "taskstore.py")
    # taskstore imports live_state_guard (2026-10-27, L2). A fixture that copies the store ALONE must
    # copy what it imports, or the copy dies with ImportError and every subprocess here returns 1 --
    # which is how this suite went red in the pre-commit hook while being green on main.
    (tmp / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(STORE.parent.parent.parent / "scripts" / "live_state_guard.py",
                tmp / "scripts" / "live_state_guard.py")
    tasks = tmp / "workspace" / "tasks"
    (tasks / "done").mkdir(parents=True)
    for tid, status in live:
        (tasks / f"{tid}--fixture.md").write_text(TASK.format(tid=tid, status=status))
    for tid, status in done:
        (tasks / "done" / f"{tid}--fixture.md").write_text(
            TASK.format(tid=tid, status=status))
    lines = ["# QUEUE.md\n"] + [f"{n}. {t} — {t} fixture"
                                for n, (t, _) in enumerate(live, 1)]
    (tasks / "QUEUE.md").write_text("\n".join(lines) + "\n")
    return app / "taskstore.py", tasks


def run(store, *args):
    return subprocess.run([sys.executable, str(store), *args],
                          capture_output=True, text=True)


def main():
    tmp = Path(tempfile.mkdtemp())
    try:
        store, tasks = sandbox(tmp, live=[("t001", "queued"), ("t002", "queued")])

        # ---- THE BUG. A shell caller is not the form, and must still be able to cancel.
        r = run(store, "cancel", "t001", "no longer needed")
        report("cancel from a non-form caller exits 0", r.returncode, 0)
        report("cancel does not raise Forged", "Forged" in r.stderr, False)

        report("cancelled file left the live directory",
               (tasks / "t001--fixture.md").exists(), False)
        moved = tasks / "done" / "t001--fixture.md"
        report("cancelled file landed in done/", moved.exists(), True)
        text = moved.read_text() if moved.exists() else ""
        report("status is cancelled, not done",
               re.search(r"^status: cancelled$", text, re.M) is not None, True)

        # The reason is the whole point of requiring one -- it has to survive to disk.
        report("the reason is on the task, whole",
               "CANCELLED: no longer needed" in text, True)
        # And it must NOT be signed in Jas's voice by a caller that is not him.
        notes = text.split("## Notes", 1)[-1]
        report("the note is attributed to the caller, not forged as Jas",
               "· jas —" in notes, False)

        report("cancelled id is out of QUEUE.md",
               "t001" in (tasks / "QUEUE.md").read_text(), False)
        report("the other task is untouched",
               "t002" in (tasks / "QUEUE.md").read_text(), True)

        # ---- a reason is required, and refusing must not half-cancel anything
        r = run(store, "cancel", "t002")
        report("cancel with no reason is refused", r.returncode, 1)
        report("a refused cancel leaves the task in the queue",
               (tasks / "t002--fixture.md").exists(), True)

        # ---- RESOLVED.md: both sides, or the view proves nothing
        run(store, "status", "t002", "done")
        run(store, "resolved")
        resolved = (tasks / "RESOLVED.md").read_text()
        cancelled_section = resolved.split("## Cancelled", 1)[-1].split("## Done")[0]
        done_section = resolved.split("## Done", 1)[-1]
        report("the cancelled task is under Cancelled",
               "t001" in cancelled_section, True)
        report("the cancelled task is NOT under Done",
               "t001" in done_section, False)
        report("the done task is under Done", "t002" in done_section, True)
        report("the done task is NOT under Cancelled",
               "t002" in cancelled_section, False)
        report("the cancellation reason is in the view",
               "no longer needed" in cancelled_section, True)

        # ---- a task coming BACK must leave the view, or the two disagree
        run(store, "status", "t002", "queued")
        resolved = (tasks / "RESOLVED.md").read_text()
        report("a reopened task leaves RESOLVED.md",
               "t002" in resolved.split("## Done", 1)[-1], False)
        report("a reopened task is back in QUEUE.md",
               "t002" in (tasks / "QUEUE.md").read_text(), True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- a fixture that redirects the store must not write the REAL RESOLVED.md.
    # Every fixture here redirects by reassigning TASKS/DONE/QUEUE/LOCK. RESOLVED.md was a
    # fifth path fixed at import, so test_task_queue.py overwrote the live view with an
    # empty one the day it was added. In-process, because the subprocess sandbox above
    # copies the module and cannot see the real file at all.
    sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
    import hashlib
    import taskstore as ts
    real = ts.TASKS / "RESOLVED.md"
    before = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    saved = (ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK)
    tmp2 = Path(tempfile.mkdtemp())
    try:
        (tmp2 / "done").mkdir()
        ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK = tmp2, tmp2 / "done", tmp2 / "QUEUE.md", tmp2 / ".lock"
        ts.write_resolved()
        report("a redirected store writes RESOLVED.md into the redirect",
               (tmp2 / "RESOLVED.md").exists(), True)
    finally:
        ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK = saved
        shutil.rmtree(tmp2, ignore_errors=True)
    after = hashlib.sha256(real.read_bytes()).hexdigest() if real.exists() else None
    report("and leaves the real RESOLVED.md byte-identical", after, before)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
