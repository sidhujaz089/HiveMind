#!/usr/bin/env python3
"""The 90% number, pinned: taskstore.escalation_rate() counts what 06 says an escalation is.

Jas's goal for this week is "90%+ of backlog items go scope -> build -> merge with NO escalation".
A goal with no instrument is a wish, so this suite fixes the arithmetic and the four markers on a
fixture store -- not on the live one, which moves every few minutes.

ORDER, SAID HONESTLY: the measurement was written first to get a number, and this oracle came after
it. The first number it produced on the live store is in the commit message, not here, because a live
count is a fact about today and not something a suite should assert.
"""
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "projects" / "app-repo"))
import taskstore as TS  # noqa: E402

PASSED = FAILED = 0
TASK = """---
id: {tid}
title: {title}
area: workspace
kind: unfiled
status: {status}
assigned: {assigned}
source: test
created: {created}
---

## Notes

{notes}
"""


def report(name, ok, detail=""):
    global PASSED, FAILED
    PASSED += bool(ok)
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        {detail}"))


def store(tmp, tasks):
    """A task tree of this suite's own, and taskstore pointed at it."""
    d = Path(tmp) / "tasks"
    (d / "done").mkdir(parents=True)
    for i, (status, assigned, notes, days_ago) in enumerate(tasks, 1):
        created = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H%M%SZ")
        name = f"t{i:03d}--fixture.md"
        where = d / "done" / name if status == "done" else d / name
        where.write_text(TASK.format(tid=f"t{i:03d}", title="fixture", status=status,
                                     assigned=assigned, created=created, notes=notes),
                         encoding="utf-8")
    TS.TASKS, TS.DONE = d, d / "done"
    TS.QUEUE, TS.LOCK = d / "QUEUE.md", d / ".lock"
    TS.write_queue([])
    return d


def main():
    with tempfile.TemporaryDirectory() as tmp:
        store(tmp, [
            ("done", "production", "", 1),                                        # clean
            ("done", "jas", "", 2),                                              # a person holds it
            ("done", "production", "**2026-10-20 · dispatch-loop** — HELD by dispatch-loop", 3),
            ("done", "production", "**2026-10-19 · claude** — NEEDS_JAS: which of these?", 4),
            ("done", "production", "**2026-10-18 · claude-code** — Ready to merge", 5),
            ("done", "production", "", 90),                                       # outside the window
            ("queued", "production", "", 2),                                      # not delivered
            ("cancelled", "production", "", 2),                                   # not delivered
        ])
        r = TS.escalation_rate(days=30)
        report("only tasks created inside the window are counted", r["created"] == 7, r["created"])
        report("a done task with no marker is the numerator", r["done_clean"] == 1, r)
        report("assignment to jas counts as an escalation", r["escalated"] == 4, r)
        report("HELD by / NEEDS_JAS / Ready to merge each count", r["escalated"] == 4, r["ids"])
        report("cancelled is counted apart, not as delivery", r["cancelled"] == 1, r)
        report("the rate is clean-done over created, as a percentage",
               abs(r["rate"] - 100.0 * 1 / 7) < 0.01, r["rate"])
        report("the window is reported so the number can be re-derived", r["since"].endswith("Z"),
               r["since"])

    with tempfile.TemporaryDirectory() as tmp:
        store(tmp, [("done", "production", "", 1), ("done", "production", "", 2)])
        r = TS.escalation_rate(days=30)
        report("a store with no escalations reads 100%, not 0 (the arithmetic, not a celebration)",
               round(r["rate"], 1) == 100.0, r)

    with tempfile.TemporaryDirectory() as tmp:
        store(tmp, [])
        r = TS.escalation_rate(days=30)
        report("an empty store reports 0.0 and does not divide by zero",
               r["rate"] == 0.0 and r["created"] == 0, r)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
