#!/usr/bin/env python3
"""t1084 -- a handover that names deferred work must say whether that work has a task.

WHY (workspace/rca/9d7774774cef.md, the 20:12:25Z row). At 86% context on 2026-09-02, 3h39m
before the session died at the ceiling, the assistant wrote (rca/9d7774774cef.md:20):

    "We're at 86%. I'd suggest letting duo finish the benchmarks and Manager tier before
    anything else -- and I'll hold the interview until the GPU is actually free rather than
    timing it out a third time."

It went into HANDOVER-2026-09-02-D.md. No task was minted. The session ended at 23:51Z with
"No task created for the deferred interview, benchmarks, or Manager tier", and six days later
the session review minted the quote as kind: deferral with covered_by: "" -- 896 candidates
consulted, no tracker to attach. scripts/handover.py today carries deferred work as prose and
has no way to notice one that names no handle, so the record that is supposed to be durable is
the one carrying the orphan.

This suite pins scripts/handover.py: DEFERRALS (the phrasings), deferred_items(text) and
deferred_checklist_rows(text, rel). The RCA's "add a deferred_work_tracked: true/false field per
deferred item in the handover JSON" is expressed here as the row text
`deferred_work_tracked: true|false`, because the handover in this repository is Markdown --
workspace/HANDOVER-<day>.md -- not JSON.

RED ON MAIN, for the stated reason and not for a bug in this file: scripts/handover.py defines
no deferred_items and no deferred_checklist_rows (verified: no .py in the tree defines either
name), so every test below records a failure naming the missing callable and the runner exits 1.
No test here imports a new module, writes outside a tempfile.TemporaryDirectory, calls a model,
or touches the live workspace.

No pytest (not installed on this box):

    python3 scripts/tests/test_handover_deferrals.py
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> the repository root
sys.path.insert(0, str(ROOT / "scripts"))

import handover                                      # noqa: E402  scripts/handover.py

# The RCA's own words, from workspace/rca/9d7774774cef.md:20 (transcript turn 1789, 20:12:25Z).
# Kept whole, on one line: a quote this suite pins is the record of a real deferral, and a
# truncated record reads as complete (docs/skills/never-truncate-a-record).
RCA_DEFERRAL = (
    "We're at 86%. I'd suggest letting duo finish the benchmarks and Manager tier before "
    "anything else -- and I'll hold the interview until the GPU is actually free rather than "
    "timing it out a third time, so the interview runs when the GPU is actually free for it."
)

FAILURES = []


def _fail(msg: str) -> None:
    FAILURES.append(msg)


def _fn(name: str):
    """The module attribute when it is callable; a recorded failure naming the gap when not."""
    fn = getattr(handover, name, None)
    if not callable(fn):
        _fail(f"scripts/handover.py defines no callable {name}() -- the deferral scan does "
              f"not exist yet")
    return fn


def _items(text):
    fn = _fn("deferred_items")
    return fn(text) if fn else None


def _rows(text, rel):
    fn = _fn("deferred_checklist_rows")
    return fn(text, rel) if fn else None


def _one(items, label):
    if not isinstance(items, list) or len(items) != 1:
        _fail(f"{label}: expected exactly 1 deferred item, got {items!r}")
        return None
    it = items[0]
    if not isinstance(it, dict) or {"quote", "task", "tracked"} - set(it):
        _fail(f"{label}: every item must be a dict carrying quote/task/tracked, got {it!r}")
        return None
    return it


def test_the_rca_deferral_is_found_and_reported_untracked():
    items = _items(RCA_DEFERRAL)
    if items is None:
        return
    it = _one(items, "the 2026-09-02 deferral line")
    if it is None:
        return
    if it["quote"] != RCA_DEFERRAL:
        _fail(f"the quote must be the WHOLE line, never sliced: got {it['quote']!r}")
    if it["task"] != "":
        _fail(f"a deferral naming no task id must report task == '' -- got {it['task']!r}")
    if it["tracked"] is not False:
        _fail(f"a deferral naming no task id must report tracked is False, a real bool "
              f"-- got {it['tracked']!r}")


def test_a_task_id_on_the_same_line_makes_it_tracked():
    items = _items(RCA_DEFERRAL + " Filed as t1084.")
    if items is None:
        return
    it = _one(items, "the deferral with a task id on its own line")
    if it is None:
        return
    if it["task"] != "t1084":
        _fail(f"the handle on the line must be reported verbatim, got {it['task']!r}")
    if it["tracked"] is not True:
        _fail(f"a deferral naming a task id on its own line must report tracked is True "
              f"-- got {it['tracked']!r}")


def test_a_task_id_on_another_line_does_not_count():
    text = RCA_DEFERRAL + "\nTracked as t1084 in workspace/tasks/QUEUE.md."
    items = _items(text)
    if items is None:
        return
    it = _one(items, "the two-line fixture")
    if it is None:
        return
    if it["tracked"] is not False or it["task"] != "":
        _fail(f"a task id on a DIFFERENT line does not track this deferral -- the handle "
              f"must be on the deferral's own line; got task={it['task']!r} "
              f"tracked={it['tracked']!r}")


def test_each_deferral_keeps_its_own_handle():
    lines = [
        "I'd suggest letting duo finish the benchmarks before anything else, filed as t1084.",
        "The Q4 interview is deferred to next session.",
        "I'll hold the interview -- written up in workspace/HANDOVER-2026-09-02-D.md.",
    ]
    items = _items("\n".join(lines))
    if items is None:
        return
    if not isinstance(items, list) or len(items) != 3:
        _fail(f"expected 3 deferred items for 3 deferral lines, got {items!r}")
        return
    tasks = [it.get("task") for it in items]
    if tasks != ["t1084", "", "workspace/HANDOVER-2026-09-02-D.md"]:
        _fail(f"each item must carry ITS OWN handle, in order -- got {tasks!r}")
    tracked = [it.get("tracked") for it in items]
    if tracked != [True, False, True]:
        _fail(f"tracked must follow the handle, item by item -- got {tracked!r}")


def test_one_item_per_line_however_many_phrases_it_carries():
    items = _items(RCA_DEFERRAL)          # names BOTH 'I'd suggest letting' and "I'll hold"
    if items is None:
        return
    if not isinstance(items, list) or len(items) != 1:
        _fail(f"a line naming two deferral phrases is ONE deferred item, got {items!r}")


def test_the_skeleton_boilerplate_is_not_a_deferral():
    with tempfile.TemporaryDirectory() as td:
        skeleton = handover.draft(Path(td), "1970-01-01", "sess-abc123")
    items = _items(skeleton)
    if items is None:
        return
    if items:
        _fail(f"the skeleton's prompt 'list unresolved issues or deferred work' is "
              f"boilerplate, not a deferral -- it must yield no items, got {items!r}")


def test_the_checklist_rows_announce_the_state():
    text = (RCA_DEFERRAL + "\n"
            + "I'd suggest letting duo finish the benchmarks first, tracked as t1084.")
    rows = _rows(text, "workspace/HANDOVER-2026-09-02.md")
    if rows is None:
        return
    if not isinstance(rows, list) or len(rows) != 2:
        _fail(f"expected one checklist row per deferred item, got {rows!r}")
        return
    untracked = [r for r in rows if "deferred_work_tracked: false" in r]
    tracked = [r for r in rows if "deferred_work_tracked: true" in r]
    if len(untracked) != 1:
        _fail(f"exactly one row must say deferred_work_tracked: false, got {rows!r}")
    if len(tracked) != 1:
        _fail(f"exactly one row must say deferred_work_tracked: true, got {rows!r}")
    if len(untracked) == 1:
        if "t1084" in untracked[0]:
            _fail(f"the untracked row must not claim a task id: {untracked[0]!r}")
        if "I'd suggest letting" not in untracked[0]:
            _fail(f"the row must name the deferred work it came from: {untracked[0]!r}")
        if "workspace/HANDOVER-2026-09-02.md" not in untracked[0]:
            _fail(f"the untracked row must name the file the deferral sits in: "
                  f"{untracked[0]!r}")
    if len(tracked) == 1 and "t1084" not in tracked[0]:
        _fail(f"the tracked row must carry its task id: {tracked[0]!r}")


def _run() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as exc:                    # a missing function is a FAILING test
                _fail(f"{name} raised {type(exc).__name__}: {exc}")
    for f in FAILURES:
        print("FAIL:", f)
    print(f"{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(_run())
