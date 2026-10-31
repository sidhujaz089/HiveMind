#!/usr/bin/env python3
"""A scoper verdict of NEEDS_JAS puts the task on hold for Jas, with the question on it.

Jas, 2026-09-14: "Yes to needs Jas checks the hold box." Before this, NEEDS_JAS was a line in
workspace/scoping-proposals.jsonl that nothing read; four questions sat there unseen.

Pinned, both directions each time:
  * NEEDS_JAS -> needs: jas, the question written on the task, the task at the bottom;
    any other verdict on a task that was never held -> nothing touched;
  * a hold Jas has NOT replied to is not re-scoped; one he HAS replied to is picked up;
  * the reply does not release the task -- only the next verdict does: anything but
    NEEDS_JAS releases it to the models, NEEDS_JAS re-holds with a fresh question.
Runs against a real taskstore pointed at a temporary tree.
"""
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))

import scope_queue as SQ   # noqa: E402
import taskstore as ts     # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


TASK = ("---\nid: {t}\ntitle: {t} fixture\narea: infra\nkind: build\nstatus: queued\n"
        "needs: model\nsource: \ncreated: 2026-01-01T000000Z\n---\n\nbody\n\n## Notes\n\n## Attachments\n")


def main():
    tmp = Path(tempfile.mkdtemp())
    tasks = tmp / "workspace" / "tasks"
    (tasks / "done").mkdir(parents=True)
    for t in ("t001", "t002", "t003"):
        (tasks / f"{t}--fixture.md").write_text(TASK.format(t=t))
    (tasks / "QUEUE.md").write_text("# QUEUE.md\n\n1. t001 — a\n2. t002 — b\n3. t003 — c\n")
    saved = (ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK, SQ.REPO)
    ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK = tasks, tasks / "done", tasks / "QUEUE.md", tasks / ".lock"
    SQ.REPO = tmp
    read = lambda t: next(tasks.glob(f"{t}--*.md")).read_text()  # noqa: E731
    try:
        # ---- NEEDS_JAS holds, with the question on the task
        acted = SQ.act_on_verdict("t001", {"verdict": "NEEDS_JAS",
                                           "question": "Which gateway should the phone use?"},
                                  store=ts, task_text=read("t001"))
        report("NEEDS_JAS is acted on", acted, "held")
        report("the hold box is set (needs: vision)", bool(re.search(r"^(needs|assigned): (jas|vision)$", read("t001"), re.M)), True)
        report("the question is written on the task",
               "SCOPER NEEDS JAS: Which gateway should the phone use?" in read("t001"), True)
        q = (tasks / "QUEUE.md").read_text()
        report("a held task goes to the bottom", q.strip().splitlines()[-1].startswith("3. t001"), True)

        # ---- other verdicts on a never-held task touch nothing
        before = read("t002")
        acted = SQ.act_on_verdict("t002", {"verdict": "NOT_DELEGABLE", "why": "design work"},
                                  store=ts, task_text=before)
        report("NOT_DELEGABLE on an unheld task does nothing", acted, None)
        report("and leaves the file byte-identical", read("t002"), before)

        # ---- an UNANSWERED hold is not picked up again
        report("an unanswered hold is not re-scoped", [x[0] for x in SQ.answered_holds(10)], [])

        # ---- Jas replies: now it is re-scopable, but still held
        ts.add_note("t001", "Use the tailnet address.", who="jas", _from_form=True)
        report("an answered hold is picked up", [x[0] for x in SQ.answered_holds(10)], ["t001"])
        report("the reply alone does not release it", bool(re.search(r"^(needs|assigned): (jas|vision)$", read("t001"), re.M)), True)

        # ---- the next verdict decides: SCOPED releases to the models
        acted = SQ.act_on_verdict("t001", {"verdict": "SCOPED", "why": "one file, suite exists"},
                                  store=ts, task_text=read("t001"))
        report("a non-NEEDS_JAS verdict after the reply releases it", acted, "released")
        report("released back to the models (Production, 2026-10-03)", bool(re.search(r"^(needs|assigned): production$", read("t001"), re.M)), True)
        # t248 (2026-09-18): the release note is now the SCOPED path note, not the old
        # RE-SCOPED line -- the contract being pinned is that the release is explained
        # on the task, attributed to the scoper.
        report("the release is explained on the task", "SCOPER SCOPED" in read("t001"), True)
        report("a released task is no longer an answered hold",
               [x[0] for x in SQ.answered_holds(10)], [])

        # ---- NEEDS_JAS again after a reply re-holds with a FRESH question, not the old one
        SQ.act_on_verdict("t003", {"verdict": "NEEDS_JAS", "question": "first question"},
                          store=ts, task_text=read("t003"))
        ts.add_note("t003", "Here is half an answer.", who="jas", _from_form=True)
        SQ.act_on_verdict("t003", {"verdict": "NEEDS_JAS", "question": "what is still missing"},
                          store=ts, task_text=read("t003"))
        report("re-held with the new question", "SCOPER NEEDS JAS: what is still missing" in read("t003"), True)
        report("and is NOT an answered hold until Jas replies again",
               [x[0] for x in SQ.answered_holds(10)], [])
    finally:
        ts.TASKS, ts.DONE, ts.QUEUE, ts.LOCK, SQ.REPO = saved

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
