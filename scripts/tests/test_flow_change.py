#!/usr/bin/env python3
"""flow_change.py: a Flow tab request becomes a task; the Manager's edit is applied ONLY when clean.

Jas, 2026-09-29 (plan step 2a-ii): a clean edit is applied, logged and revertable; one warning or one
consequence is Assign to: Jas with nothing applied. A temporary task store and FLOW.json -- the real
ones are never touched.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import flow_change as fc  # noqa: E402
import flow_lint  # noqa: E402
import taskstore  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


def meta(tid):
    return taskstore.find(tid)[0] or {}


def notes(tid):
    return taskstore.find(tid)[1] or ""


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tasks = tmp / "tasks"
        tasks.mkdir()
        taskstore.TASKS, taskstore.DONE = tasks, tasks / "done"
        taskstore.QUEUE, taskstore.LOCK = tasks / "QUEUE.md", tasks / ".lock"
        flow = tmp / "FLOW.json"
        shutil.copy2(flow_lint.FLOW, flow)
        fc.FLOW = flow_lint.FLOW = flow
        fc.LOG = tmp / "FLOW-CHANGELOG.jsonl"
        original = flow.read_text(encoding="utf-8")
        first = json.loads(original)["tasks"][0]

        tid = fc.request("edit_task", first["id"], "say what this box does more plainly")
        report("a request becomes a task at the top of the Backlog", taskstore.read_queue()[:1], [tid])
        report("  recognised as a Flow change", fc.is_flow(tid), True)
        report("  carrying Jas's own words", "say what this box does more plainly" in notes(tid), True)
        try:
            fc.request("describe", "", "")
            empty_refused = False
        except ValueError:
            empty_refused = True
        report("an empty described change is refused, not filed", empty_refused, True)

        ans = tmp / "answer.md"
        ans.write_text("(c) SCOPE -- a plainer note.\n\n```flow-edit\n" + json.dumps(
            [{"op": "edit_task", "id": first["id"], "set": {"note": "What this box does, plainly."}}]) + "\n```\n")
        r = fc.answer(tid, str(ans))
        report("a clean edit is applied and the task closed", r, ("done", ""))
        report("  FLOW.json now carries it",
               json.loads(flow.read_text())["tasks"][0]["note"], "What this box does, plainly.")
        rows = fc.entries()
        report("  logged with its before and after", (len(rows), rows[0]["before"]["tasks"][0].get("note")),
               (1, first.get("note")))
        report("  and the note says how to undo it", "Revert change 1" in notes(tid), True)

        n = fc.revert(1)
        report("a revert restores the snapshot, byte-identical in content",
               json.loads(flow.read_text()), json.loads(original))
        report("  and is itself an entry (so it can be undone)", (n, len(fc.entries())), (2, 2))

        tid2 = fc.request("delete_task", first["id"], "we do not need this")
        ans.write_text("(c) SCOPE\n```flow-edit\n" + json.dumps([{"op": "delete_task", "id": first["id"]}]) + "\n```\n")
        before = flow.read_text()
        r = fc.answer(tid2, str(ans))
        report("an edit with a consequence is NOT applied: Assign to: Jas", r, ("queued", "jas"))
        report("  FLOW.json is untouched", flow.read_text(), before)
        report("  the note says what it would have done, in plain words",
               "NOT APPLIED" in notes(tid2) and "deletes the task" in notes(tid2), True)

        tid3 = fc.request("describe", "", "make the chart nicer somehow")
        ans.write_text("(b) NEEDS JAS -- which part of the chart do you mean?\n")
        report("a question from the Manager goes to Jas", fc.answer(tid3, str(ans)), ("queued", "jas"))
        ans.write_text("I'll read FLOW.json first, then write the edit.")
        report("a one-line promise decides nothing (retried, not sent to Jas)", fc.answer(tid3, str(ans)), None)
        ans.write_text("(c) SCOPE\n```flow-edit\nnot json\n```\n")
        report("a broken block is sent to Jas, nothing applied", fc.answer(tid3, str(ans)), ("queued", "jas"))
        report("  and FLOW.json is still the original", json.loads(flow.read_text()), json.loads(original))
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
