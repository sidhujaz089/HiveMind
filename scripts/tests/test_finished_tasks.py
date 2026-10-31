#!/usr/bin/env python3
"""t155: finished tasks, newest first, and who did each -- Claude or a local model.

Jas: "a list of tasks that have been completed sorted by order they were completed ... I also want
to see what you did vs local model." RED on main: scripts/finished_tasks.py is a stub with no
finished()/render(). GREEN when finished(tasks, verdicts) orders the done tasks by completion,
newest first, and names the builder from Claude's merge-review verdicts (a 'kept' verdict names the
model that built it; no kept verdict means Claude did it), and render() shows that as a table.
Pure: fixture task metas and verdict rows.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import finished_tasks as ft  # noqa: E402

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


TASKS = [
    {"id": "t1", "title": "Old <one>", "status": "done", "completed": "2026-09-01T100000Z", "area": "hub"},
    {"id": "t2", "title": "Newest", "status": "done", "completed": "2026-10-02T100000Z", "area": "hub"},
    {"id": "t3", "title": "Middle", "status": "done", "completed": "2026-09-15T100000Z", "area": "checks"},
    {"id": "t4", "title": "Dropped", "status": "cancelled", "completed": "2026-09-20T100000Z"},
    {"id": "t5", "title": "Open", "status": "queued"},
    {"id": "t6", "title": "No date", "status": "done"},
]
VERDICTS = [
    {"task": "t3", "verdict": "kept", "served": "gemma4:26b"},
    {"task": "t2", "verdict": "sent-back", "served": "gemma4:26b"},     # sent back: Claude finished it
    {"task": "t1", "verdict": "kept", "served": "gemma4:26b", "repaired": "fixed a line"},
]


def main():
    if not (hasattr(ft, "finished") and hasattr(ft, "render")):
        print("  [BAD]  finished_tasks has no finished()/render(): the missing change")
        return 1
    f = ft.finished(TASKS, VERDICTS)
    ck("only done tasks, newest completion first; an undated one last", [r["id"] for r in f], ["t2", "t3", "t1", "t6"])
    by = {r["id"]: r["by"] for r in f}
    ck("a kept verdict names the local model that built it", by.get("t3"), "gemma4:26b")
    ck("no kept verdict (or only a sent-back one): Claude", (by.get("t2"), by.get("t6")), ("Claude", "Claude"))
    ck("a repaired keep is still the model's build, marked repaired", (by.get("t1"), f[2].get("repaired")), ("gemma4:26b", True))
    h = ft.render(f)
    ck("render links each task and shows its date and builder",
       all(x in h for x in ('href="/plan/t2"', "2026-10-02", "gemma4:26b", "Claude")), True)
    ck("titles are HTML-escaped", ("&lt;one&gt;" in h, "<one>" in h), (True, False))
    ck("render counts who did what", ("Claude: 2" in h, "gemma4:26b: 2" in h), (True, True))
    ck("nothing finished renders a plain line", "Nothing finished yet" in ft.render([]), True)
    print("test_finished_tasks:", "FAILED" if FAILS else "ok")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
