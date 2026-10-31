#!/usr/bin/env python3
"""t412 oracle: the manager prompt pins its task to a QUEUE.md LINE (and says its
list position), and a 'QUEUE.md:N' citation is checked against that line -- a list position
must never pass as a line. The real queue has a 9-line header, so position N sits on line
N+9: that offset is the observed bug (t123 'QUEUE.md:88 (position 88)' for an item on line
97; t323 'QUEUE.md:12 (position 3)'). This fixture reproduces the offset with a known line.

Fail-first against today's scripts/manager_prompt.py: main() takes no repo argument
(manager_prompt.py:57), the JOB header names no queue line/position
(manager_prompt.py:30), and manager_prompt has no build_prompt / resolve_queue_citations
(whole file read, 69 lines). Expected to fail until the implementation lands.

House style: report(name, got, want); run directly with python3 (done_when: exits 0).
Temp dirs and fakes only; no live state, no network.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import manager_prompt  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = (got == want)
    PASSED += 1 if ok else 0
    FAILED += 0 if ok else 1
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def capture(tid, root):
    """(rc, prompt string) for one task: main() for the return code, build_prompt for the text."""
    rc = manager_prompt.main([tid], repo=str(root))
    out = manager_prompt.build_prompt(tid, root)
    return rc, out


# t300 is list position 3, line 14: the position and the line DIFFER, exactly like the real queue.
FIXTURE_QUEUE = """\
# QUEUE.md -- fixture queue, header offset like the real one

line 3 filler
line 4 filler
line 5 filler
line 6 filler
line 7 filler
line 8 filler
line 9 filler
line 10 filler

1. t100 -- first task
2. t200 -- second task
3. t300 -- third task
4. t400 -- fourth task
"""

FIXTURE_TASK = """\
---
id: t300
title: third task
status: queued
---

## Notes

**2026-09-25 -- fixture** -- the task the manager is asked to work.
"""


def fixture_repo():
    root = Path(tempfile.mkdtemp(prefix="t412-prompt-"))
    tasks = root / "workspace" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "QUEUE.md").write_text(FIXTURE_QUEUE, encoding="utf-8")
    (tasks / "t300--third-task.md").write_text(FIXTURE_TASK, encoding="utf-8")
    return root


def test_prompt_pins_task_to_queue_line():
    root = fixture_repo()
    rc, out = capture("t300", root)
    report("main returns 0 for a listed task", rc, 0)
    report("the prompt hands the model the task's QUEUE.md line AND its position",
           "t300 -- QUEUE.md:14 (queue position 3)" in out, True)
    report("the prompt states QUEUE.md:N means a line, never a list position",
           "means line N of workspace/tasks/QUEUE.md" in out, True)


def test_unlisted_task_invents_neither_line_nor_position():
    root = fixture_repo()
    rc, out = capture("t500", root)
    report("main returns 0 for an unlisted task", rc, 0)
    report("a missing task file is reported", "(no task file for t500)" in out, True)
    report("an unlisted task is not given a line or a position", "(not listed in QUEUE.md)" in out, True)
    report("...and no 'queue position' appears anywhere", "queue position" in out, False)


def test_missing_queue_file_is_reported_not_crashed():
    root = Path(tempfile.mkdtemp(prefix="t412-noqueue-"))
    tasks = root / "workspace" / "tasks"
    tasks.mkdir(parents=True)
    (tasks / "t300--third-task.md").write_text(FIXTURE_TASK, encoding="utf-8")
    rc, out = capture("t300", root)
    report("main returns 0 when QUEUE.md is absent", rc, 0)
    report("an absent queue is reported, not crashed", "(not listed in QUEUE.md)" in out, True)


def test_citation_on_the_true_line_resolves():
    root = fixture_repo()
    queue = str(root / "workspace" / "tasks" / "QUEUE.md")
    rows = manager_prompt.resolve_queue_citations(
        "I worked the task at QUEUE.md:14 (position 3).", queue, "t300")
    report("one citation, one row", len(rows), 1)
    if rows:
        report("a citation on the task's true line passes", rows[0]["ok"], True)
        report("claimed 14, actual 14", (rows[0]["claimed"], rows[0]["actual"]), (14, 14))


def test_position_cited_as_line_fails():
    """The t323 shape: 'QUEUE.md:3 (position 3)' -- the position wearing the line's clothes."""
    root = fixture_repo()
    queue = str(root / "workspace" / "tasks" / "QUEUE.md")
    rows = manager_prompt.resolve_queue_citations(
        "I worked the task at QUEUE.md:3 (position 3).", queue, "t300")
    report("one citation, one row", len(rows), 1)
    if rows:
        report("a QUEUE.md:N that is the position, not the line, is flagged", rows[0]["ok"], False)
        report("claimed 3, actual 14", (rows[0]["claimed"], rows[0]["actual"]), (3, 14))


def main():
    test_prompt_pins_task_to_queue_line()
    test_unlisted_task_invents_neither_line_nor_position()
    test_missing_queue_file_is_reported_not_crashed()
    test_citation_on_the_true_line_resolves()
    test_position_cited_as_line_fails()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
