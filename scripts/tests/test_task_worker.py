#!/usr/bin/env python3
"""Model tasks added on the Flow tab get a worker (plan step 4): queue, prompt, outcome, the next
task. Fakes only: a temp FLOW and queue root, a fake model, a fake task filer."""
import copy
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import task_worker as tw  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


F = {"tasks": [
    {"id": "summarise", "name": "Summarise", "model_task": True, "prompt": "Summarise it.",
     "outcomes": ["short", "long", "needs person", "odd", "done"], "ends": ["done"]},
    {"id": "polish", "name": "Polish", "model_task": True, "outcomes": ["ok"], "ends": ["ok"]},
    {"id": "backlog", "name": "Backlog"}, {"id": "assigned_jas", "name": "Assigned to Jas"},
    {"id": "preflight", "name": "Preflight"}],
    "links": [{"from": "summarise", "outcome": "short", "to": "polish"},
              {"from": "summarise", "outcome": "long", "to": "backlog"},
              {"from": "summarise", "outcome": "needs person", "to": "assigned_jas"},
              {"from": "summarise", "outcome": "odd", "to": "preflight"}]}


def main():
    tmp = Path(tempfile.mkdtemp(prefix="task-worker-test-"))
    filed = []

    def filer(title, body, assigned=""):
        filed.append((title, assigned, body))
        return f"t9{len(filed):02d}"
    try:
        t = F["tasks"][0]
        report("only model tasks have a queue", _raises(lambda: tw.send("preflight", "x", root=tmp, f=F)), True)
        p = tw.send("summarise", "a long report about X", who="Jas - Human", root=tmp, f=F)
        report("send puts an item file in the task's queue", [x[1].name for x in tw.pending(tmp, F)], [p.name])
        pr = tw.prompt_for(t, json.loads(p.read_text()))
        report("the prompt carries the task's instructions, the item and every outcome",
               all(s in pr for s in ("Summarise it.", "a long report about X", "short | long | needs person")), True)
        report("the LAST OUTCOME line is read", tw.outcome_of("OUTCOME: <x>\n...\nOUTCOME: Short", t), "short")
        report("an undeclared outcome is not an outcome", tw.outcome_of("OUTCOME: maybe", t), None)
        report("no OUTCOME line -> None", tw.outcome_of("I think it is short.", t), None)
        report("routes: next model task / backlog / person / end / built-in",
               [tw.route(t, o, F)[0] for o in ("short", "long", "needs person", "done", "odd")],
               ["task", "backlog", "person", "end", "stuck"])

        # a good answer moves the item on to the next model task
        r = tw.run("summarise", p, "m", root=tmp, f=F, ask=lambda pr, m, task: (0, "It says X.\nOUTCOME: short"),
                   filer=filer)
        report("a good answer: exit 0", r, 0)
        report("  the item left the queue and is kept in done/ with the answer",
               (p.exists(), json.loads((p.parent / "done" / p.name).read_text())["result"]), (False, "short"))
        nxt = tw.pending(tmp, F)
        report("  and the answer is now an item on the next task", [x[0] for x in nxt], ["polish"])
        report("  saying where it came from", json.loads(nxt[0][1].read_text())["from"]["task"], "summarise")

        p2 = tw.send("summarise", "second", root=tmp, f=F)
        tw.run("summarise", p2, "m", root=tmp, f=F, ask=lambda *a: (0, "OUTCOME: needs person"), filer=filer)
        report("an outcome linked to Assigned to Jas files a task assigned to jas", filed[-1][1], "jas")
        p3 = tw.send("summarise", "third", root=tmp, f=F)
        tw.run("summarise", p3, "m", root=tmp, f=F, ask=lambda *a: (0, "OUTCOME: odd"), filer=filer)
        report("a link into a built-in task with its own queue: filed for Claude, not dropped",
               (filed[-1][1], "built-in task" in filed[-1][2]), ("claude", True))

        # a bad answer is counted, then held for Claude
        p4 = tw.send("summarise", "fourth", root=tmp, f=F)
        n = len(filed)
        r = tw.run("summarise", p4, "m", root=tmp, f=F, ask=lambda *a: (0, "no outcome here"), filer=filer)
        report("no OUTCOME line: exit 1, the item stays queued, nothing filed",
               (r, p4.exists(), len(filed) - n), (1, True, 0))
        r = tw.run("summarise", p4, "m", root=tmp, f=F, ask=lambda *a: (0, "still none"), filer=filer)
        report("second bad answer: held and filed for Claude with the answers kept",
               (p4.exists(), filed[-1][1], len(json.loads((p4.parent / 'done' / p4.name).read_text())["answers"])),
               (False, "claude", 2))
        p5 = tw.send("summarise", "fifth", root=tmp, f=F)
        report("the model's lease was held: exit 4, nothing counted",
               (tw.run("summarise", p5, "m", root=tmp, f=F, ask=lambda *a: (4, ""), filer=filer),
                json.loads(p5.read_text())["attempts"]), (4, 0))

        # the dispatcher lists the items for the role that owns the task, and runs task_worker for them
        import dispatch_loop as dl
        import task_routing as tr
        real = (tw.pending, dl.ROUTING, dl.ROLES)
        try:
            tw.pending = lambda root=None, f=None: [("summarise", p5)]
            D = copy.deepcopy(tr.DEFAULT)
            D["roles"]["manager"]["tasks"].insert(2, {"task": "summarise", "interrupt": False, "teamwork": False})
            dl.ROUTING, dl.ROLES = D, {"manager": "q4", "auditor": "q4", "senior": "q4", "coder": "g"}
            got = [i for i in dl.priority_items([], [], None, [], [], preflight=False) if i.get("model_task")]
            report("the dispatcher lists a model task's item for the role whose list has it",
                   [(i["kind"], i["role"], i["model"]) for i in got], [("summarise", "manager", "q4")])
            D["roles"]["manager"]["tasks"] = [x for x in D["roles"]["manager"]["tasks"] if x["task"] != "summarise"]
            got = [i for i in dl.priority_items([], [], None, [], [], preflight=False) if i.get("model_task")]
            s, pre, q, res = dl.schedule(["q4"], got, [])
            report("no role lists it: it waits, and says why", (s, "no role has" in q[0][1]), ([], True))
        finally:
            tw.pending, dl.ROUTING, dl.ROLES = real
        src = (ROOT / "scripts" / "dispatch_loop.py").read_text()
        report("run_job sends a model task to task_worker.py run", '"task_worker.py"), "run", job["kind"]' in src, True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


def _raises(fn):
    try:
        fn()
    except KeyError:
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
