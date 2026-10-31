#!/usr/bin/env python3
"""close_landed.py: a task closes when its code merged AND its own tests are green on main -- and not otherwise.

Jas, 2026-10-31: "did you fix why merged fix doesn't close the task?" 16 merged harness tasks with green oracles sat at
"doing" for up to three weeks, because only the scoper's "already done" ever closed anything. move_main must start it.
Fixtures only: a temp ledger, temp task files, a fake store and a fake test runner.

Run: python3 scripts/tests/test_close_landed.py
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import close_landed as CL  # noqa: E402

FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {name}" + ("" if ok else f"\n         got: {detail}"))


class Store:
    def __init__(self, refuse=()):
        self.done, self.notes, self.refuse = [], [], set(refuse)

    def add_note(self, tid, text, who=None):
        self.notes.append((tid, text))

    def set_status(self, tid, status):
        if tid in self.refuse:
            return False
        self.done.append(tid)
        return True


with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    led = d / "sq.jsonl"
    led.write_text("\n".join(json.dumps(r) for r in [
        {"task": "t1", "stage": "merge", "outcome": "merged"},
        {"task": "t2", "stage": "merge", "outcome": "merged"},
        {"task": "t3", "stage": "freeze", "outcome": "merged"},      # only the ORACLE was frozen: not a code merge
        {"task": "t4", "stage": "merge", "outcome": "merged"},
        {"task": "t5", "stage": "merge", "outcome": "merged"},
        {"task": "t6", "stage": "merge", "outcome": "merged"},
    ]) + "\n")
    tasks = d / "tasks"
    tasks.mkdir()
    for t, st in (("t1", "doing"), ("t2", "doing"), ("t3", "doing"), ("t4", "done"), ("t5", "doing"), ("t6", "doing")):
        (tasks / f"{t}--x.md").write_text(f"---\nid: {t}\nstatus: {st}\n---\n")
    (d / "scripts" / "tests").mkdir(parents=True)
    for n in ("green", "red"):
        (d / "scripts" / "tests" / f"test_{n}.py").write_text("")
    tests = {"t1": ["scripts/tests/test_green.py"], "t2": ["scripts/tests/test_red.py"],
             "t3": ["scripts/tests/test_green.py"], "t5": [], "t6": ["scripts/tests/test_green.py"]}
    run = lambda t: 0 if "green" in t else 1                                   # noqa: E731
    store = Store(refuse={"t6"})
    res = {t: (c, e) for t, c, e in CL.sweep(ledger=led, tasks_dir=tasks, repo=d, tests_for=lambda t, b: tests.get(t, []),
                                            run=run, store=store)}
    check("a merged task whose tests are green on main is CLOSED, with the evidence noted",
          res.get("t1", (False,))[0] and "t1" in store.done and any(t == "t1" and "test_green" in n for t, n in store.notes),
          res)
    check("a merged task whose test is red stays open, and says which test", res.get("t2", (True,))[0] is False
          and "test_red" in res["t2"][1], res.get("t2"))
    check("an oracle FREEZE is not a code merge: that task is not even judged", "t3" not in res, res)
    check("a task already done is not touched again", "t4" not in res, res)
    check("no test defining done -> not closed, and said", res.get("t5", (True,))[0] is False and "no test" in res["t5"][1],
          res.get("t5"))
    check("taskstore's refusal (it still owns a red allowed-to-fail suite) stands, and is said",
          res.get("t6", (True,))[0] is False and "refused" in res["t6"][1] and "t6" not in store.done, res.get("t6"))
    store2 = Store()
    CL.sweep(dry_run=True, ledger=led, tasks_dir=tasks, repo=d, tests_for=lambda t, b: tests.get(t, []), run=run,
             store=store2)
    check("--dry-run changes nothing", not store2.done and not store2.notes, (store2.done, store2.notes))

src = (REPO / "scripts" / "merge_reviewed.py").read_text(encoding="utf-8")
body = src.split("def _move(", 1)[1].split("\ndef ", 1)[0]
check("merge_reviewed._move starts close_landed after main moves", "close_landed.py" in body
      and body.index("update-ref") < body.index("close_landed.py"), "not found after update-ref")

print(f"test_close_landed: {'ok' if not FAILED else f'{FAILED} failed'}")
sys.exit(1 if FAILED else 0)
