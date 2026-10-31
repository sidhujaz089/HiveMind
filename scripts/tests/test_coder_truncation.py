#!/usr/bin/env python3
"""The contract for telling a CUT-OFF answer from a bad one.

WHY. On 2026-09-08 two dispatches burned an hour each and were reported as
`unusable (unterminated JSON object, 16000 tok)` and `unusable (no JSON object in the reply,
13954 tok)`. Both were the output budget, not the model: `ask()` returns
`(text, eval_count, elapsed)` and NEVER READS `done_reason`, so Ollama stopping because
`length` is indistinguishable from the model finishing.

`harness_inventory.py` already classifies `coder.py` as `monitored` for exactly
`no-stop-reason`. The audit found it, printed it, nobody acted, and it cost two hours --
"detection without repair is just documentation", which is the sentence every one of the nine
reviewers pulled out of draft four.

Jas, 2026-09-08: *"can you fix coder. it's ask()? It needs to be able to recover from a cut
off."* Recovery, not just reporting: a truncated attempt must not be spent as a failed
attempt, and it must be retryable with more room.

THE ORACLE IS WRITTEN HERE AND THE IMPLEMENTATION IS DELEGATED. coder.py is given
projects/local-inference/coder.py and never this file.
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
sys.path.insert(0, str(ROOT / "scripts"))
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


import coder                                                 # noqa: E402

ask = getattr(coder, "ask", None)
check("coder.ask exists", callable(ask), True)

print("\nask() reports WHY the model stopped -- the whole defect in one line:")
sig = inspect.signature(ask)
src = inspect.getsource(ask)
check("ask() reads done_reason from the response", "done_reason" in src, True)

Truncated = getattr(coder, "Truncated", None)
check("coder.Truncated exists -- a cut-off is its own outcome, not a bad answer",
      isinstance(Truncated, type) and issubclass(Truncated, Exception), True)

print("\nThe caller can ask for more room, so a cut-off is RECOVERABLE:")
check("ask() takes a max_tokens argument", "max_tokens" in sig.parameters, True)
grow = getattr(coder, "MAX_TOKENS_RETRY", None)
check("a larger retry budget is declared", isinstance(grow, int) and grow > 16000, True)

print("\nA truncated attempt is not spent as a failed attempt:")
# The 2026-09-08 cost: five attempts, two of them burned entirely on truncation, reported to
# the operator as the model producing garbage. A cut-off must be retried with more room before
# it counts against the attempt budget.
body = inspect.getsource(coder)
check("the retry path exists and names truncation",
      "Truncated" in body and body.count("Truncated") >= 2, True)

print("\nThe operator is told the difference, because the log is the only thing they see:")
check("the word 'truncated' reaches the operator's log",
      "truncated" in body.lower(), True)
check("and it is not silently folded into 'unusable'",
      "unusable" in body and "truncated" in body.lower(), True)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
