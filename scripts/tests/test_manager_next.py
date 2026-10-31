#!/usr/bin/env python3
"""manager_next picks the first QUEUE.md task that needs manager work. Fixtures only."""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import manager_next as MN  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        tasks, ses, briefs = t / "tasks", t / "ses", t / "briefs"
        for d in (tasks, ses, briefs):
            d.mkdir()
        q = tasks / "QUEUE.md"
        q.write_text("1. t1 — waits on Jas\n2. t2 — being built\n3. t3 — worked today\n"
                     "4. t4 — builder only today\n5. t5 — free\n6. t6 — done\n7. t7 — held for Claude\n")
        # t1 carries the field task files ACTUALLY use since the 2026-09-29 migration (`assigned:`);
        # t7 the legacy `needs:`. Until 2026-09-30 both used `needs:`, so this passed while every
        # real held task was being worked again (the Manager found it, on t485).
        for tid, extra in [("t1", "assigned: jas\n"), ("t2", ""), ("t3", ""), ("t4", ""),
                           ("t5", ""), ("t6", ""), ("t7", "needs: claude\n")]:
            status = "done" if tid == "t6" else "queued"
            (tasks / f"{tid}--x.md").write_text(f"---\nid: {tid}\nstatus: {status}\n{extra}---\n")
        (briefs / "brief-p9.txt").write_text("TASK: t2\n\nONE file")
        loop = t / "loop.sh"
        loop.write_text("p9-x|brief-p9.txt|a.py|t.py\n")
        for tid, role in [("t3", None), ("t4", "builder")]:
            (ses / tid).mkdir()
            (ses / tid / "s.jsonl").write_text(json.dumps({"kind": "start", "role": role}) + "\n")
        kw = dict(queue=q, tasks=tasks, sessions=ses, loop=loop, briefs=briefs, now=time.time())
        picked, skipped = MN.upcoming(2, **kw)
        all3, _ = MN.upcoming(3, **kw)
        (ses / "t5").mkdir()
        (ses / "t5" / "s.jsonl").write_text(json.dumps({"kind": "start"}) + "\n")
        old = time.time() - 3 * 86400
        os.utime(ses / "t5" / "s.jsonl", (old, old))
        rot, _ = MN.upcoming(3, **kw)
    report("the first free task in queue order, then the next", picked, ["t4", "t5"])
    report("  skipping waiting-on-Jas, in-production and worked, with reasons",
           [s[0] for s in skipped], ["t1", "t2", "t7", "t3"])
    report("a worked task is not held: it comes after the fresh ones", all3, ["t4", "t5", "t3"])
    report("worked tasks rotate longest-ago first", rot, ["t4", "t5", "t3"])
    report("a builder session does not count as manager work", "t4" in picked, True)
    report("a done task is not picked", "t6" in picked, False)
    # A BLANK FIELD IS BLANK (2026-10-02): `\s*` ran past the newline, so a released task's empty
    # `assigned:` read as the next header line ('source: claude-code').
    import tempfile as _tf
    _d = Path(_tf.mkdtemp())
    (_d / "t9--x.md").write_text("---\nid: t9\nassigned: \nsource: claude-code\n---\nbody\n")
    report("a blank assigned: reads as blank, not as the next line", MN.meta("t9", tasks=_d).get("needs"), "")
    report("a task held for Claude is never picked", "t7" in all3 + rot, False)
    report("  and says why", ("t7", "held for Claude") in skipped, True)
    # ASSIGNED TO A MODEL ROLE (plan 3b): assigned_worker answers it; scoping must not take it too.
    # "model" (scope_queue's old internal value, 80 live tasks) is not a role and stays pickable.
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        tasks, ses = t / "tasks", t / "ses"
        tasks.mkdir()
        ses.mkdir()
        q = tasks / "QUEUE.md"
        q.write_text("1. t1 — to the manager\n2. t2 — to the senior\n3. t3 — legacy model\n4. t4 — free\n")
        for tid, who in [("t1", "manager"), ("t2", "senior"), ("t3", "model"), ("t4", "")]:
            (tasks / f"{tid}--x.md").write_text(f"---\nid: {tid}\nstatus: queued\nassigned: {who}\n---\n")
        order, skipped = MN.upcoming(4, queue=q, tasks=tasks, sessions=ses, loop=t / "none.sh", briefs=t)
    report("a task assigned to a model role is left to its Assigned items worker", order, ["t3", "t4"])
    # THE BIG-PROJECT FLAG: ticked, no work starts until Jas unticks it; unticked, it is ordinary work.
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        tasks = t / "tasks"
        tasks.mkdir()
        (t / "ses").mkdir()
        (tasks / "QUEUE.md").write_text("1. t1 — big\n2. t2 — not big\n")
        for tid, box in (("t1", "x"), ("t2", " ")):
            (tasks / f"{tid}--x.md").write_text(f"---\nid: {tid}\nstatus: queued\nassigned: \n---\n\n"
                                               f"- [{box}] big project -- check this to require Jas's go-ahead\n")
        order2, skipped2 = MN.upcoming(2, queue=tasks / "QUEUE.md", tasks=tasks, sessions=t / "ses",
                                       loop=t / "none.sh", briefs=t)
    report("a ticked big project is not started; an unticked one is", order2, ["t2"])
    report("  and says why", ("t1", "big project: waiting for Jas's go-ahead") in skipped2, True)
    report("  and says why", ("t1", "assigned to manager (Assigned items)") in skipped, True)
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        tasks, ses, briefs = t / "tasks", t / "ses", t / "briefs"
        for d in (tasks, ses, briefs):
            d.mkdir()
        q = tasks / "QUEUE.md"
        q.write_text("1. t1 — scoped\n2. t2 — answered, unusable\n3. t3 — released after\n4. t4 — fresh\n")
        for tid, st in [("t1", "doing"), ("t2", "queued"), ("t3", "queued"), ("t4", "queued")]:
            (tasks / f"{tid}--x.md").write_text(f"---\nid: {tid}\nstatus: {st}\n---\n")
        old = time.time() - 3600
        for tid in ("t2", "t3"):
            (ses / tid).mkdir()
            (ses / tid / "s.jsonl").write_text(json.dumps({"kind": "start"}) + "\n")
            os.utime(ses / tid / "s.jsonl", (old, old))
        os.utime(tasks / "t2--x.md", (old - 60, old - 60))     # unchanged since the pass
        kw = dict(queue=q, tasks=tasks, sessions=ses, loop=t / "none.sh", briefs=briefs)
        order, _ = MN.upcoming(4, **kw)
    report("a doing (scoped) task is never the manager's", "t1" in order, False)
    report("a task changed after its pass (released, noted) is fresh, in queue order",
           order, ["t3", "t4", "t2"])
    # A SPENT CHUNK IS NOT WORK (2026-10-13, t1115): a chunk whose target is already green on main no
    # longer keeps its task "in production" -- the manager skipped t1115 and the board showed it nowhere.
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "briefs").mkdir()
        (t / "briefs" / "brief-p1-t9-a.txt").write_text("TASK: t9\n")
        (t / "briefs" / "brief-p2-t8-b.txt").write_text("TASK: t8\n")
        (t / "loop.sh").write_text("p1-t9-a|brief-p1-t9-a.txt|a.py|scripts/tests/test_a.py\n"
                                   "p2-t8-b|brief-p2-t8-b.txt|b.py|scripts/tests/test_b.py\n")
        (t / "tick.json").write_text(json.dumps({"skipped": [
            {"chunk": "p1-t9-a", "why": "target green -- already on main"},
            {"chunk": "p2-t8-b", "why": "waiting -- every model has a lane"}]}))
        spent = MN.spent_chunks(t / "tick.json")
        report("the last tick's 'target green' chunks are spent", spent, {"p1-t9-a"})
        report("a task whose chunks are all spent is not in production; one still to build is",
               MN.in_production(t / "loop.sh", t / "briefs", spent=spent), {"t8"})
    # equal data (2026-10-19): each scoper prefers its half of the task numbers, never idles
    o = ["t10", "t11", "t12", "t13"]
    report("the Manager takes even task numbers first", MN.prefer_slot(o, "manager", harness=lambda t: False), ["t10", "t12", "t11", "t13"])
    report("the Auditor takes odd task numbers first", MN.prefer_slot(o, "auditor", harness=lambda t: False), ["t11", "t13", "t10", "t12"])
    report("with none of its own left a scoper takes any task", MN.prefer_slot(["t10"], "auditor", harness=lambda t: False), ["t10"])
    report("no scoper named: queue order unchanged", MN.prefer_slot(o, "", harness=lambda t: False), o)
    h = lambda t: t == "t13"
    report("a HARNESS task goes first for either scoper", MN.prefer_slot(o, "manager", harness=h)[0], "t13")
    report("and ahead of the split for the other", MN.prefer_slot(o, "auditor", harness=h)[:2], ["t13", "t11"])
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
