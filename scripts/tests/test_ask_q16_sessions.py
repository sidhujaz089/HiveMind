#!/usr/bin/env python3
"""ask_q16 records every round into the task session and starts from earlier sessions (t386).

Fake task_sessions and fake console; never the model, never the live workspace.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]

rows = []
fake = types.ModuleType("task_sessions")
fake.append = lambda path, kind, **f: rows.append((path, kind, f))
fake.start = lambda task, model, purpose, root=None: f"/fake/{task}.jsonl"
fake.digest = lambda task, max_chars=40000, root=None: "=== session by q4: earlier ===\nQ4 ANSWER"
sys.modules["task_sessions"] = fake

import ask_q16  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


class FakeConsole:
    RUN = {"read_file": lambda a: "FULL FILE " + "x" * 5000}

    def __init__(self):
        self.n = 0

    def ask(self, messages, **k):
        self.n += 1
        if self.n == 1:
            return {"message": {"content": "", "thinking": "look first",
                                "tool_calls": [{"function": {"name": "read_file",
                                                             "arguments": {"path": "a.py"}}}]},
                    "done_reason": "stop"}
        return {"message": {"content": "DONE", "thinking": "now answer"}, "done_reason": "stop"}


def main():
    ask_q16.SESSION = None
    ask_q16._rec("model", content="x")
    report("no task -> nothing recorded", rows, [])

    ask_q16.SESSION = "/fake/t9.jsonl"
    m, why, rnd = ask_q16.run_tool_rounds([{"role": "user", "content": "go"}], FakeConsole(), 4,
                                          100, lambda *a: None)
    kinds = [k for _p, k, _f in rows]
    report("each model turn and each tool call is recorded", kinds, ["model", "tool", "model"])
    report("  the tool result is stored WHOLE",
           len(next(f for _p, k, f in rows if k == "tool")["result"]), len("FULL FILE ") + 5000)
    report("  the model's reasoning is kept", rows[0][2]["thinking"], "look first")

    src = (ROOT / "scripts" / "ask_q16.py").read_text()
    report("--task exists", '"--task"' in src, True)
    report("a run with --task starts from the digest of earlier sessions",
           "prompt = PRIOR_HEADER + prior" in src, True)
    report("  and tells the model the history is a record, not verified fact",
           "NOT verified facts" in ask_q16.PRIOR_HEADER, True)
    report("the answer and the reasoning are recorded at the end",
           ('_rec("answer"' in src, '_rec("reasoning"' in src), (True, True))

    def boom(*a, **k):
        raise OSError("disk full")
    fake.append = boom
    ask_q16._rec("model", content="y")
    report("a session row that cannot be written does not stop the run", True, True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
