#!/usr/bin/env python3
"""scope_quality.py: every check on a scope is recorded against the model that wrote it, and the
first-pass yield scores that model (Jas, 2026-09-28). Temporary folders only."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts")]
import scope_quality as sq  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="sq-test-"))
    try:
        sessions, answers = tmp / "sessions", tmp / "out"
        for task, model in (("t1", "qwen-a"), ("t2", "qwen-a"), ("t3", "qwen-b")):
            (sessions / task).mkdir(parents=True)
            (sessions / task / f"2026-{model}.jsonl").write_text(
                json.dumps({"kind": "start", "model": model}) + "\n"
                + json.dumps({"kind": "prompt", "text": "scope this"}) + "\n")
        answers.mkdir()
        (answers / "t1.md").write_text("answer one")
        sq.SESSIONS, sq.ANSWERS = sessions, answers
        led, dfx = tmp / "led.jsonl", tmp / "defects"
        kw = {"ledger": led, "defects": dfx}

        s = sq.scope_of("t1")
        report("a scope names its task, the answer's hash and the model from the task session",
               (s["scope"].startswith("t1@"), s["model"], bool(s["prompt_version"])), (True, "qwen-a", True))
        report("an unknown task is 'unknown', never a guess", sq.model_of("t9")[0], "unknown")

        sq.record("bridge", "branch", task="t1", **kw)
        sq.record("merge", "merged", task="t1", **kw)
        sq.record("lint", "failed", task="t2", defect_class="oracle-green", evidence="exit 0 on main", **kw)
        sq.record("merge", "merged", task="t3", **kw)
        sq.record("build", "failed exit 1", task="t3", defect_class="harness", **kw)
        sq.record("bridge", "branch", task="t3", **kw)
        sc = sq.score(sq.rows(led))
        report("clean merge counts clean; a scope-class defect counts against the model",
               (sc["qwen-a"]["final"], sc["qwen-a"]["clean"], sc["qwen-a"]["first_pass_yield"]), (2, 1, 0.5))
        report("a harness defect is recorded but is not the scope's fault",
               (sc["qwen-b"]["first_pass_yield"], sc["qwen-b"]["by_class"]), (1.0, {"harness": 1}))
        report("models are scored separately (a later Qwen gets its own line)", sorted(sc), ["qwen-a", "qwen-b"])
        report("every defect waits as a file for its RCA", len(sq.pending_defects(dfx)), 2)
        sq.record("lint", "failed", task="t2", defect_class="oracle-green", evidence="exit 0 on main", **kw)
        report("  the same defect twice is one file", len(sq.pending_defects(dfx)), 2)
        n_rows = len(sq.rows(led))
        again = sq.record("lint", "failed", task="t2", defect_class="oracle-green", evidence="exit 0 on main", **kw)
        report("  and ONE ROW: a repeat is returned marked, not appended (t266 had 130)",
               (len(sq.rows(led)), (again or {}).get("duplicate")), (n_rows, True))
        dup = sq.rows(led) + [r for r in sq.rows(led) if r.get("key")] * 3
        report("  rows written before the fix are counted once by score()",
               sq.score(dup)["qwen-a"]["scope_defects"], sq.score(sq.rows(led))["qwen-a"]["scope_defects"])

        # the RCA's attribution replaces the first guess
        k = json.loads(next(f for f in sq.pending_defects(dfx) if "oracle-green" in f.read_text()).read_text())["key"]
        sq.record("rca", "classified", task="t2", defect_class="", defect_key=k, **kw)
        rows = sq.rows(led)
        rows[-1]["class"] = "harness"
        led.write_text("".join(json.dumps(r) + "\n" for r in rows))
        report("an RCA that reclassifies to harness lifts the scope's fault",
               sq.score(sq.rows(led))["qwen-a"]["first_pass_yield"], 1.0)

        # THE LOOP: defects -> the Manager's queue, one task per class, later ones noted on it
        class Store:
            def __init__(self):
                self.tasks, self.queue, self.notes = {}, [], []
            def read_queue(self):
                return list(self.queue)
            def find(self, tid):
                return ({"id": tid, "title": self.tasks[tid]}, "") if tid in self.tasks else ({}, "")
            def add(self, title, **kw):
                tid = f"t{900 + len(self.tasks)}"
                self.tasks[tid] = title
                self.queue.append(tid)
                self.body = kw.get("body", "")
                return tid
            def move(self, tid, where):
                self.queue.remove(tid)
                self.queue.insert(0, tid)
            def add_note(self, tid, text, who=""):
                self.notes.append((tid, text))
        st = Store()
        st.tasks["t800"], st.queue = "something else", ["t800"]
        dfx2 = tmp / "defects2"
        sq.record("lint", "failed", task="t1", defect_class="oracle-no-runner", evidence="a", ledger=led, defects=dfx2)
        sq.record("build", "failed", task="t1", defect_class="oracle-no-runner", evidence="b", ledger=led, defects=dfx2)
        sq.record("supervise", "harness-bug", task="t1", defect_class="harness", evidence="c", ledger=led, defects=dfx2)
        before = sq.score(sq.rows(led))["qwen-a"]["scope_defects"]
        acts = [a for _k, a in sq.route(defects=dfx2, store=st, ledger=led)]
        report("the first defect of a class files ONE task at the top of the queue",
               (st.queue[0], st.tasks[st.queue[0]]), ("t901", "Scope RCA: oracle-no-runner"))
        report("  a second of the same class is a note on it, not a new task",
               ("noted on t901" in acts, len([t for t in st.tasks.values() if t.startswith("Scope RCA")])), (True, 1))
        report("  harness defects are left to the supervisor's HARNESS tasks",
               any(a.startswith("skipped") for a in acts), True)
        report("  the task asks for the whole RCA skill and an upstream fix",
               ("root-cause-analysis/SKILL.md IN FULL" in st.body, "scope_lint" in st.body), (True, True))
        report("  routed defects leave the waiting list", sq.pending_defects(dfx2), [])
        report("  routing does not erase the defects from the score",
               sq.score(sq.rows(led))["qwen-a"]["scope_defects"], before)
        report("recording never raises into its caller", sq.record("x", "y", task="t1",
               ledger=Path("/nonexistent-dir-xyz/l.jsonl"), defects=dfx), None)
        report("the report prints the model, the yield and n", "clean 1 of 1" in sq.report(ledger=led), True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
