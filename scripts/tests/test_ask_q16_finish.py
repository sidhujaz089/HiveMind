#!/usr/bin/env python3
"""A manager answer cut off at the limit is FINISHED from the model's own reasoning, thinking off.

2026-09-23: seven of nine manager runs ended done_reason=length with 0 chars (tool results
filled the window) and ask_q16 discarded the reasoning. Against a fake console, never the model.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import ask_q16  # noqa: E402
import console  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    calls = []

    def fake_ask(messages, tools=True, budget=None, level=None, holder=None, timeout=None,
                 model_name=None, num_ctx=None, role="manager"):
        calls.append({"tools": tools, "level": level, "content": messages[-1]["content"],
                      "n": len(messages)})
        if len(calls) == 1:
            return {"message": {"content": "", "thinking": "I read QUEUE.md; t272 fits p05."},
                    "done_reason": "length"}
        return {"message": {"content": "p05 -> t272"}, "done_reason": "stop"}

    console.ask = fake_ask
    r = ask_q16.ask("TASK: map the chunks", use_tools=False, budget=100, max_escalations=0,
                    verbose=False)
    report("a cut-off answer is finished from its reasoning", r["text"], "p05 -> t272")
    report("  and says how it was written", r["done_reason"], "finished-from-reasoning")
    report("  the finish call has thinking OFF", calls[-1]["level"], "off")
    report("  and no tools", calls[-1]["tools"], False)
    report("  and carries the task and the reasoning",
           ("TASK: map the chunks" in calls[-1]["content"], "t272 fits p05" in calls[-1]["content"]),
           (True, True))
    report("  and not the tool history (one message)", calls[-1]["n"], 1)
    report("the reasoning is returned so it can be kept", "t272 fits p05" in r["thinking"], True)

    # A FINISH THAT PROMISES WORK IS NOT AN ANSWER. 2026-09-29/30: ten manager answers came
    # back finished-from-reasoning as ONE sentence -- "I'll do a few targeted reads to ground
    # the uncertain claims before writing" -- with no tools left to do them, and manager_note
    # held every one for Jas, who could do nothing with it. Seven of seven, the same shape.
    deferral = ("I'll do a quick verification pass on the current line numbers before I "
                "finalize the scope, then write the answer.")
    for second, want_text, name in (
            ("STEP: (c) SCOPE\n\nthe real answer", "STEP: (c) SCOPE\n\nthe real answer",
             "a deferral is asked again, once, and the real answer kept"),
            (deferral, "", "a deferral twice is NOT returned as an answer")):
        calls.clear()
        replies = iter([{"message": {"content": "", "thinking": "I read QUEUE.md."},
                         "done_reason": "length"},
                        {"message": {"content": deferral}, "done_reason": "stop"},
                        {"message": {"content": second}, "done_reason": "stop"}])

        def fake_ask2(messages, tools=True, budget=None, level=None, holder=None, timeout=None,
                      model_name=None, num_ctx=None, role="manager"):
            calls.append({"tools": tools, "content": messages[-1]["content"]})
            return next(replies, {"message": {"content": ""}, "done_reason": "length"})

        console.ask = fake_ask2
        r = ask_q16.ask("TASK: scope it", use_tools=False, budget=100, max_escalations=0,
                        verbose=False)
        report(name, r["text"] if r["done_reason"] == "finished-from-reasoning" else "",
               want_text)
    report("  the re-ask says there are no tools and no more turns",
           "NO TOOLS" in calls[2]["content"] if len(calls) > 2 else None, True)
    report("a real answer is not mistaken for a deferral",
           ask_q16.is_deferral("STEP: (b) NEEDS JAS\nLet me explain: I'll need you to pick one."),
           False)
    report("a long answer that opens with a plan is not a deferral",
           ask_q16.is_deferral("I'll verify the lines first. " + "x " * 600), False)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
