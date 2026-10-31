#!/usr/bin/env python3
"""Fixtures for the plan: the queue drift check, and the task store's two-writer safety.

docs/CHECKS.md: a check is not trusted until it has been observed FAILING on a fixture built
to break it. Every assertion here is a case where the bug would look like success --

  * a task file no queue line points at is invisible in the plan, so it silently never gets
    done, and the queue still renders perfectly;
  * a queue line naming a task that is not on disk promises work that does not exist;
  * a task listed twice gets two positions and one of them is a lie;
  * and concurrent note writers lose notes SILENTLY -- the file is well-formed afterwards,
    it is just missing what someone wrote.

The last one is why the lock exists, and it is asserted here by removing the lock and
requiring the loss to reappear. A lock nobody has watched fail is a lock nobody has tested.
"""
import concurrent.futures as cf
import contextlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


# --------------------------------------------------------------------- the drift check

TASK = ("---\nid: {tid}\ntitle: {tid} fixture\narea: workspace\nstatus: {status}\n"
        "source: \ncreated: 2026-01-01T000000Z\n---\n\nbody\n\n## Notes\n\n## Attachments\n")


def build(tmp, files, queue_ids, done=()):
    """A minimal repository shaped like the real one, then run the real check against it."""
    # check_workspace refuses a folder with no .git, and it is right to.
    subprocess.run(["git", "init", "-q", str(tmp)], check=True,
                   capture_output=True)
    tasks = tmp / "workspace" / "tasks"
    tasks.mkdir(parents=True)
    (tmp / "projects").mkdir()
    for tid, status in files:
        (tasks / f"{tid}--fixture.md").write_text(TASK.format(tid=tid, status=status))
    for tid, status in done:
        (tasks / "done").mkdir(exist_ok=True)
        (tasks / "done" / f"{tid}--fixture.md").write_text(TASK.format(tid=tid, status=status))
    lines = ["# QUEUE.md\n"]
    lines += [f"{n}. {t} — {t} fixture" for n, t in enumerate(queue_ids, 1)]
    (tasks / "QUEUE.md").write_text("\n".join(lines) + "\n")


def run_check(files, queue_ids, done=()):
    """The check's verdict on this fixture: PASS or FAIL, read off the real script."""
    tmp = Path(tempfile.mkdtemp())
    try:
        build(tmp, files, queue_ids, done)
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_workspace.py"),
             "--quick", "--repo-root", str(tmp)],
            capture_output=True, text=True).stdout
        if "The plan" not in out:
            return "ABSENT"
        block = []
        for line in out.split("The plan", 1)[1].splitlines()[1:]:
            if line and not line.startswith(" "):
                break                      # next section heading
            block.append(line)
        block = "\n".join(block)
        return "FAIL" if "[FAIL]" in block else ("PASS" if "[PASS]" in block else "ABSENT")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_fixtures():
    print("The queue drift check -- it must FAIL on each of these:")
    report("the healthy case still passes",
           run_check([("t001", "queued"), ("t002", "queued")], ["t001", "t002"]), "PASS")
    report("task on disk that no queue line names",
           run_check([("t001", "queued"), ("t002", "queued")], ["t001"]), "FAIL")
    report("queue line naming a task that is not there",
           run_check([("t001", "queued")], ["t001", "t009"]), "FAIL")
    report("task listed twice in the queue",
           run_check([("t001", "queued")], ["t001", "t001"]), "FAIL")
    report("done task still sitting in the queue folder",
           run_check([("t001", "done")], ["t001"]), "FAIL")
    report("file in done/ whose status is not done",
           run_check([], [], done=[("t007", "queued")]), "FAIL")


# --------------------------------------------------------------------- the lock

def lock_fixture():
    print("\nThe task store's lock -- 24 concurrent writers on one file:")
    import taskstore as ts
    tmp = Path(tempfile.mkdtemp())
    ts.TASKS, ts.DONE = tmp, tmp / "done"
    ts.QUEUE, ts.LOCK = tmp / "QUEUE.md", tmp / ".lock"
    try:
        tid = ts.add("lock fixture", "workspace")

        def survivors():
            with cf.ThreadPoolExecutor(24) as ex:
                list(ex.map(lambda n: ts.add_note(tid, f"w{n:02d}"), range(24)))
            _, body = ts.find(tid)
            return sum(1 for i in range(24) if f"w{i:02d}" in body)

        report("every writer's note survives", survivors(), 24)

        # Remove the lock and require the loss to come back. Without this the test would
        # pass just as happily on a store that never needed a lock at all.
        meta, body = ts.find(tid)
        ts._rewrite(meta, body.split("## Notes")[0] + "\n## Notes\n\n## Attachments\n")
        real, ts.locked = ts.locked, lambda: contextlib.nullcontext()
        try:
            lost = survivors() < 24
        finally:
            ts.locked = real
        report("notes are lost once the lock is removed", lost, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def review_flag_fixture():
    """L-089: a note from Jas must NOT silently clear needs:jas.

    The old behaviour emptied his review queue and the emptiness looked like good news.
    """
    print("\nThe review flag -- a reply must not make a task disappear:")
    import shutil, tempfile
    import taskstore as ts
    tmp = Path(tempfile.mkdtemp())
    ts.TASKS, ts.DONE = tmp, tmp / "done"
    ts.QUEUE, ts.LOCK = tmp / "QUEUE.md", tmp / ".lock"
    try:
        tid = ts.add("standing instruction", "workspace", needs="jas")
        ts.add_note(tid, "acknowledged, but I still need this later",
                    who="jas", _from_form=True)   # simulating the form
        meta, body = ts.find(tid)
        report("still flagged after his reply", meta["needs"], "jas")
        report("and it is visible as replied", ts.last_speaker(body), "jas")
        ts.set_meta(tid, needs="claude")
        report("Code can clear it explicitly", ts.find(tid)[0]["needs"], "technical") # needs: is a tier (2026-10-29)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def forgery_fixture():
    """L-091: Code must not be able to write a note in Jas's voice.

    It did, twice, while testing the note thread -- and both were caught by chance. A note
    attributed to him enters at the top tier of the firewall and is never questioned again.
    """
    print("\nAttribution -- only the form may speak as Jas:")
    import shutil, tempfile
    import taskstore as ts
    tmp = Path(tempfile.mkdtemp())
    ts.TASKS, ts.DONE = tmp, tmp / "done"
    ts.QUEUE, ts.LOCK = tmp / "QUEUE.md", tmp / ".lock"
    try:
        tid = ts.add("attribution fixture", "workspace")

        refused = False
        try:
            ts.add_note(tid, "he never said this", who="jas")
        except ts.Forged:
            refused = True
        report("a note attributed to jas is REFUSED", refused, True)

        _, body = ts.find(tid)
        report("and nothing was written", "he never said this" in body, False)

        # The form is the one legitimate source, because there it is true by construction.
        ts.add_note(tid, "typed on the phone", who="jas", _from_form=True)
        report("the form may still do it", "typed on the phone" in ts.find(tid)[1], True)

        # Code's own voice is unaffected.
        ts.add_note(tid, "my own note", who="claude-code")
        report("claude-code is unaffected", "my own note" in ts.find(tid)[1], True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def completed_fixture():
    """A task's completed timestamp: set on first done, preserved on re-done, removed on un-done."""
    print("\nThe completed timestamp -- when a task finishes, when it was, and when it un-finishes:")
    import shutil, tempfile
    import re as _re
    import taskstore as ts
    tmp = Path(tempfile.mkdtemp())
    ts.TASKS, ts.DONE = tmp, tmp / "done"
    ts.QUEUE, ts.LOCK = tmp / "QUEUE.md", tmp / ".lock"
    try:
        tid = ts.add("completion fixture", "workspace")
        ts.set_status(tid, "done")
        meta, _ = ts.find(tid)
        report("completed is set when task is done", "completed" in meta, True)
        report("completed looks like a UTC timestamp",
               bool(_re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{6}Z", meta.get("completed", ""))), True)
        original = meta["completed"]
        ts.set_status(tid, "queued")
        ts.set_status(tid, "done")
        meta2, _ = ts.find(tid)
        report("re-completion preserves original timestamp", meta2["completed"], original)
        ts.set_status(tid, "queued")
        meta3, _ = ts.find(tid)
        report("completed is removed when task leaves done", "completed" in meta3, False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def machine_above_jas_fixture():
    """t264: a task Claude or a model makes goes directly above Jas's first task; his order stays."""
    print("\nNew machine tasks go above Jas's (t264):")
    import taskstore as ts
    tmp = Path(tempfile.mkdtemp())
    real = (ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK)
    ts.TASKS, ts.DONE = tmp, tmp / "done"
    ts.QUEUE, ts.LOCK = tmp / "QUEUE.md", tmp / ".lock"
    try:
        m1 = ts.add("machine one", source="claude-code")
        j1 = ts.add("jas one", source="added from the phone")
        j2 = ts.add("jas two", source="workspace/app-feedback/x.md")
        ts.move(j2, "top")                              # Jas sorts one by hand, above everything
        m2 = ts.add("machine two", source="session-rca")
        j3 = ts.add("jas three", source="jas")
        report("a machine task lands directly above Jas's first task, his hand-sorted order kept",
               ts.read_queue(), [m2, j2, m1, j1, j3])
    finally:
        ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK = real
        shutil.rmtree(tmp, ignore_errors=True)


def hold_goes_to_bottom_fixture():
    """t576: `taskstore.py hold` moves the task to the bottom (Jas, 2026-09-14: "If a held item will
    block the flow, we need a rule that moved held items to the bottom")."""
    print("\nA held task goes to the bottom (t576):")
    import taskstore as ts
    tmp = Path(tempfile.mkdtemp())
    real = (ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK)
    ts.TASKS, ts.DONE = tmp, tmp / "done"
    ts.QUEUE, ts.LOCK = tmp / "QUEUE.md", tmp / ".lock"
    try:
        a, b, c = (ts.add(x, source="jas") for x in ("a", "b", "c"))
        ts.main(["hold", a])
        report("taskstore hold moves the task to the bottom", ts.read_queue(), [b, c, a])
    finally:
        ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK = real
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    hold_goes_to_bottom_fixture()
    machine_above_jas_fixture()
    check_fixtures()
    forgery_fixture()
    lock_fixture()
    review_flag_fixture()
    completed_fixture()
    print(f"\n{PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
