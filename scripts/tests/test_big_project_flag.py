#!/usr/bin/env python3
"""The contract for the big-project flag: Jas can stop work he has not agreed to start.

WHY. On 2026-09-08 Jas labelled 20 backlog items SCOPED, then said: "some stuff on the list
could be considered scoped but I think they are bigger projects so I want to be clear with you
before they start." Delegability and commitment are DIFFERENT QUESTIONS -- an item can be
perfectly specified and still commit weeks. Folding them into one label is the mistake that
produced a 38% agreement figure comparing two definitions of "scoped".

So it is a separate flag, not a status. It appears in every task file and in every idea
arriving from the phone, and it defaults to unchecked so nothing is silently marked.

    - [ ] big project -- check this to require Jas's go-ahead before any work starts

THE ORACLE IS WRITTEN HERE AND THE IMPLEMENTATION IS DELEGATED.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
PASSED = FAILED = 0
MARK = re.compile(r"^- \[( |x|X)\] big project", re.M)


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


# A STORE OF ITS OWN, NOT THE LIVE ONE (2026-10-27, L2). Until now this suite snapshotted and
# restored the REAL queue -- "a test that mutates the thing it is testing must put it back" -- and
# the restore was better than nothing but never true isolation: it drew a REAL id out of
# workspace/tasks/.ids on every run, and a crash before the restore left a throwaway task in the
# live queue (t249, 2026-09-08). HIVEMIND_TASKS_DIR is read at import, so it is set first and the
# suite's own directory is the whole store. Nothing needs putting back.
STORE = Path(tempfile.mkdtemp(prefix="big-project-")) / "tasks"
STORE.mkdir(parents=True, exist_ok=True)
(STORE / "QUEUE.md").write_text("")
os.environ["HIVEMIND_TASKS_DIR"] = str(STORE)
import taskstore                                             # noqa: E402

QUEUE = STORE / "QUEUE.md"
_queue_before = QUEUE.read_text()

print("\nNew tasks carry NO flag (Jas, 2026-10-07: the models start what they find):")
tid = taskstore.add("a throwaway task for the big-project flag test", "unfiled", source="test")
f = sorted(STORE.glob(f"{tid}--*.md"))[0]
body = f.read_text()
m = MARK.search(body)
check("no checkbox is written", bool(m), False)
check("the task still has its Notes section", "## Notes" in body, True)
# Jas can still add the box by hand, above the notes:
body = body.replace("## Notes", "- [ ] big project -- check this to require Jas's go-ahead before any work starts\n\n## Notes", 1)
f.write_text(body)

print("\nThe flag is READABLE, so a run can refuse to start one:")
is_big = getattr(taskstore, "is_big_project", None)
check("taskstore.is_big_project exists", callable(is_big), True)
if callable(is_big):
    check("unchecked reads False", is_big(tid), False)
    f.write_text(body.replace("- [ ] big project", "- [x] big project"))
    check("checked reads True", is_big(tid), True)
    # FIXED 2026-09-30: this read `(f.write_text(...) or is_big(tid))`, and write_text returns the
    # number of characters written, so the `or` never called is_big and the case could not pass.
    f.write_text(f.read_text().replace("- [x]", "- [X]"))
    check("a capital X also counts", is_big(tid), True)

print("\nAn unknown task is not silently 'not big':")
if callable(is_big):
    got = None
    try:
        got = is_big("t999999")
    except Exception as e:                                   # noqa: BLE001
        got = type(e).__name__
    check("it raises or returns None rather than False", got is not False, True)

print("\nIdeas arriving from the phone carry it too:")
# The intake path writes workspace/app-feedback/*.md. An idea Jas sends from his phone is
# exactly where a big project is most likely to be described in one line, so the flag has to
# be there and not only on tasks promoted later.
intake = getattr(taskstore, "feedback_template", None)
check("a shared intake template exists", callable(intake), True)
if callable(intake):
    check("and it carries no flag (2026-10-07)", bool(MARK.search(intake())), False)

print("\nExisting task files are not rewritten behind Jas's back:")
# Seeded here, because the store is this suite's own: an old-shaped file with no flag line at all,
# which is what "existing task files" means. It used to be found in the real queue.
(STORE / "t000--an-old-task-made-before-the-flag.md").write_text(
    "---\nid: t000\ntitle: an old task made before the flag\narea: workspace\nkind: unfiled\n"
    "status: queued\nassigned: production\nsource: test\ncreated: 2026-09-01T000000Z\n---\n\n"
    "## Notes\n", encoding="utf-8")
old = sorted(STORE.glob("t0*.md"))
if old:
    check("an old task without the flag still parses",
          callable(is_big) and is_big(old[0].stem.split("--")[0]) is False, True)

f.unlink(missing_ok=True)
QUEUE.write_text(_queue_before)                              # put the queue back, always
print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
