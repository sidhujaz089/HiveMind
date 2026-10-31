#!/usr/bin/env python3
"""Resuming a truncated attempt instead of restarting it (t238).

Jas, 2026-09-14: "How come every retry resets the timer? Is there no way that the
model can resume from the point of failure and just pivot versus starting from
scratch?"

A reply cut off at num_predict used to be indistinguishable from a malformed one.
Both arrived as an unparseable string, the reasoning was discarded, and the model
reasoned from zero into the same ceiling. The 2026-09-14 lanes ran attempts to the
full 16,000 tokens back to back this way.

LIVE-VERIFIED before this fixture was written: qwen3.8:27b-mtp-q4_K_M forced to
done_reason=length at 60 tokens replied "" (coder.parse: "no JSON object in the
reply" -- the exact error on those lanes), and finish_truncated() turned its 162
chars of reasoning into a valid ops object in 88 tokens and 6 seconds.

WHAT IS PINNED, and why each matters:
  * the reasoning is handed back WHOLE when it fits -- a resume from a prefix is a
    restart with extra steps;
  * the finish call runs with reasoning OFF and a small budget -- otherwise it can
    truncate the same way and the pivot is another reset;
  * when the reasoning cannot fit, the cut is DECLARED to the model in words and
    keeps the END, where the decision was forming;
  * the loop counts RESUMED as the end of an attempt, not a new one, so resuming
    cannot eat the attempt budget.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))

import coder   # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {str(got)[:70]}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {str(got)[:70]}")


def main():
    calls = []

    def fake(prompt, model, think=True, max_tokens=16000, timeout=None):
        calls.append({"prompt": prompt, "think": think, "max_tokens": max_tokens})
        return '{"ops": [{"path": "a.py", "old": "x", "new": "y"}]}', 42, 1.0

    REASON = "Step one. " + "consider the anchor " * 200 + "DECISION: change x to y."
    last = {"done_reason": "length", "thinking": REASON, "content": ""}

    # ---- fits: whole reasoning, reasoning off, small budget
    raw, tok, secs, note = coder.finish_truncated("PROMPT", "m", last, ask_fn=fake,
                                                  num_ctx=65536)
    c = calls[-1]
    report("the finish call gets the reasoning whole", REASON in c["prompt"], True)
    report("the finish call runs with reasoning OFF", c["think"], False)
    report("the finish call has a small budget", c["max_tokens"], coder.FINISH_TOKENS)
    report("it is told it was cut off, not that it was wrong",
           "RAN OUT OF ROOM" in c["prompt"], True)
    report("it is told not to start over", "Do not start over" in c["prompt"], True)
    report("the original task is still in front of it", c["prompt"].startswith("PROMPT"), True)
    report("a fitting resume is recorded as in full", "all" in note, True)
    report("the reply comes back parseable", coder.parse(raw)[0] is not None, True)

    # ---- does not fit: keep the END, and SAY what was dropped
    calls.clear()
    raw, tok, secs, note = coder.finish_truncated("PROMPT", "m", last, ask_fn=fake,
                                                  num_ctx=coder.FINISH_TOKENS + 1200)
    c = calls[-1]
    report("an oversize reasoning keeps its end (where the decision is)",
           "DECISION: change x to y." in c["prompt"], True)
    report("an oversize reasoning drops its beginning", "Step one." in c["prompt"], False)
    report("the cut is declared to the model with numbers",
           re.search(r"FROM CHARACTER \d+ OF \d+", c["prompt"]) is not None, True)
    report("the cut is recorded in the note", "dropped" in note, True)

    # ---- no room at all: say so, do not call the model with a broken prompt
    calls.clear()
    raw, tok, secs, note = coder.finish_truncated("P" * 400000, "m", last, ask_fn=fake,
                                                  num_ctx=8192)
    report("no room: no model call is made", len(calls), 0)
    report("no room: says why", "no room" in note, True)

    # ---- the loop wiring, read from source: RESUMED must not consume an attempt, and
    #      the resume must be triggered by done_reason=length, not by any parse failure
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    report("RESUMED is excluded from the attempt count",
           'not in ("ASKED", "RESUMED")' in src, True)
    report("the resume is driven by the Truncated outcome, not any parse failure",
           'except Truncated as e:' in src, True)
    report("ask() records how the call ended",
           '"done_reason": d.get("done_reason")' in src, True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
