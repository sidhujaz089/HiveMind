#!/usr/bin/env python3
"""The manager FINISH prompt names the block: no tools, no more turns (t1119).

RCA bec1ac7a8bcb, cause C3 (most upstream): ask_q16's finish prompt (FINISH_TAIL, in
scripts/ask_q16.py) told a cut-off model only "do not look anything up again" -- never that
its tools and turns are GONE. So "I'll do a few targeted reads, then write the answer" was the
natural first output. DEFERRAL_REASK says the block only AFTER the promise has been written and
logged: detection, not prevention. This suite pins the statement in the FIRST finish prompt,
where it can prevent the promise.

Runs against a fake console: no model call, no live server, no subprocess, no /tmp, no process
left running.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]

import ask_q16   # noqa: E402
import console   # noqa: E402

PASSED = FAILED = 0

TASK = "TASK: scope t1119 -- name the block in the manager finish prompt"
THINKING = "I read QUEUE.md; I still want to open scripts/ask_q16.py before writing the brief."
ANSWER = "STEP: (c) SCOPE\n\nthe scoped chunks"
DEFER = ("I'll do a quick verification pass on the current line numbers before I "
         "finalize the scope, then write the answer.")
REASON_MARKER = "=== YOUR REASONING SO FAR ==="


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" +
          ("" if ok else f"  (got {got!r}, want {want!r})"))


def drive(replies):
    """Run ask_q16.ask over a fake console. Returns (every prompt content, the result)."""
    seen = []

    def fake(messages, tools=True, budget=None, level=None, holder=None, timeout=None,
             model_name=None, num_ctx=None, role="manager"):
        seen.append(str(messages[-1].get("content") or ""))
        return next(replies, {"message": {"content": ""}, "done_reason": "length"})

    console.ask = fake
    r = ask_q16.ask(TASK, use_tools=False, budget=100, max_escalations=0, verbose=False)
    return seen, r


def main():
    # 1. A cut-off answer reaches its finish call, and THAT prompt names the block.
    seen, r = drive(iter([{"message": {"content": "", "thinking": THINKING},
                           "done_reason": "length"},
                          {"message": {"content": ANSWER}, "done_reason": "stop"}]))
    report("the cut-off run reaches its finish call", len(seen) >= 2, True)
    finish = seen[1] if len(seen) > 1 else ""
    low = finish.lower()
    cut = finish.find(REASON_MARKER)
    report("the finish prompt says there are NO TOOLS", "no tools" in low, True)
    report("the finish prompt says there are NO MORE TURNS", "no more turns" in low, True)
    report("the finish prompt demands the FINAL ANSWER now", "final answer" in low, True)
    # The block must be in the INSTRUCTION, not echoed inside the model's own reasoning.
    report("the block is in the instruction, not echoed from the reasoning",
           (cut >= 0 and 0 <= low.find("no tools") < cut), True)
    # The 2026-09-24 reasoning hand-off must survive the edit: a rewrite that drops it passes
    # every assertion above and re-creates the seven-lost-answers bug.
    report("the finish prompt still carries the task", TASK in finish, True)
    report("the finish prompt still carries the reasoning",
           "open scripts/ask_q16.py" in finish, True)
    report("and the real answer is kept", r["text"], ANSWER)

    # 2. A promise is asked again, once -- and the re-ask keeps naming the block.
    seen, r = drive(iter([{"message": {"content": "", "thinking": THINKING},
                           "done_reason": "length"},
                          {"message": {"content": DEFER}, "done_reason": "stop"},
                          {"message": {"content": ANSWER}, "done_reason": "stop"}]))
    report("a promise of work is asked again, exactly once", len(seen), 3)
    reask = seen[2] if len(seen) > 2 else ""
    rlow = reask.lower()
    report("the re-ask prompt names the block",
           ("no tools" in rlow) and ("no more turns" in rlow), True)
    report("and the real answer after the re-ask is kept", r["text"], ANSWER)

    # 3. A second promise is discarded, never returned as an answer.
    seen, r = drive(iter([{"message": {"content": "", "thinking": THINKING},
                           "done_reason": "length"},
                          {"message": {"content": DEFER}, "done_reason": "stop"},
                          {"message": {"content": DEFER}, "done_reason": "stop"}]))
    report("a second promise is NOT returned as an answer", r["text"], "")

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
