#!/usr/bin/env python3
"""GATE: a review that runs out of budget mid-thought must still produce a verdict.

Jas, 2026-09-15: "Truncation is the issue to solve and not thinking."

He is right, and the mechanism says why. Reasoning and the answer draw on ONE budget --
num_predict -- and nothing reserves room for the answer, so a long enough reasoning pass
returns done_reason=length with an EMPTY content field. settings.py records the measurement
that proves it, on gemma4:26b with the prompt "Reply with exactly one word: RESTART":

    think=high num_predict=500   18.8s  done=stop    content 'RESTART'
    think=high num_predict=100    1.1s  done=length  content ''   <- ALL 100 on thinking

So lowering the reasoning level only makes the squeeze less likely. It cannot make it
impossible, because a bigger brief buys back the same failure.

THIS REPOSITORY ALREADY SOLVED IT, TWICE, AND THE REVIEWER MISSED BOTH.
  console.py:800   on done_reason=length it re-asks with reasoning OFF, handing the model
                   back its own unfinished thinking, so the whole budget goes to the reply.
  coder.py:724,871 ask(raise_on_length=True) raises Truncated and the loop resumes from the
                   model's own reasoning with thinking off -- that is t238, fixed 2026-09-14.
  preflight_brief.py:387  gives up. Records TRUNCATED, returns 2, and the chunk parks.

That is L-114's shape -- a fix applied to one caller of a shared idea and not the others --
and console.py's own docstring names L-114 while committing it. On 2026-09-15 it cost three
truncated reviews and parked both chunks that were in flight.

THE PROPERTY, not the instance: whatever the budget and whatever the reasoning level, a
review that reasoned but did not answer must come back and answer from what it had.
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


import preflight_brief as pf                                # noqa: E402

# The telemetry ledger is a real file; a test must not write rows to it.
import run_telemetry                                        # noqa: E402
run_telemetry.record = lambda **kw: None

THINKING = "I checked rule 1 and rule 2 and was partway through rule 3 when I ran out"
VERDICT = "VERDICT: SATISFIABLE\nWHY: every path named exists.\nMISSING: nothing"

calls = []
replies = []
orig_urlopen = urllib.request.urlopen


class _Reply:
    def __init__(self, body):
        self._b = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _fake_urlopen(req, timeout=None):
    # ONLY /api/chat. The first version of this oracle answered EVERY urlopen, so a
    # settings probe consumed the scripted "length" reply and the review received the
    # scripted verdict instead -- and the suite went green with the defect untouched.
    # A test that cannot tell which caller it is answering is not measuring anything.
    if not req.full_url.endswith("/api/chat"):
        raise AssertionError(f"unexpected request to {req.full_url} -- this oracle "
                             f"scripts /api/chat only")
    calls.append(json.loads(req.data.decode()))
    return _Reply(replies[len(calls) - 1])


print("\nA review cut off mid-thought comes back and ANSWERS, instead of parking a chunk:")
calls.clear()
replies[:] = [
    {"message": {"content": "", "thinking": THINKING}, "done_reason": "length"},
    {"message": {"content": VERDICT}, "done_reason": "stop"},
]
urllib.request.urlopen = _fake_urlopen
try:
    out, why = pf.ask("the work order", "qwen3.8:27b-mtp-bf16", num_predict=12000,
                  timeout=600)
except Exception as e:                                       # noqa: BLE001
    out, why = f"RAISED {type(e).__name__}: {e}", None
finally:
    urllib.request.urlopen = orig_urlopen

check("it asked a second time rather than giving up", len(calls), 2)
if len(calls) == 2:
    second = calls[1]
    check("the second ask has reasoning OFF, so the budget is all answer",
          bool(second.get("think")), False)
    check("it hands the model back its own unfinished reasoning",
          THINKING in json.dumps(second.get("messages") or []), True)
check("a verdict comes back", "SATISFIABLE" in out, True)
check("and it is NOT reported as a truncation", why == "length", False)
check("the salvage is recorded, not hidden", getattr(pf.ask, "salvaged", None), True)

print("\nA review that answered first time is NOT asked twice:")
calls.clear()
replies[:] = [{"message": {"content": VERDICT}, "done_reason": "stop"}]
urllib.request.urlopen = _fake_urlopen
try:
    out2, why2 = pf.ask("the work order", "qwen3.8:27b-mtp-bf16", num_predict=12000,
                  timeout=600)
finally:
    urllib.request.urlopen = orig_urlopen
check("exactly one request", len(calls), 1)
check("reported as a clean stop", why2, "stop")
check("and not marked salvaged", getattr(pf.ask, "salvaged", None), False)

print("\nIf the salvage ITSELF cannot answer, that is still not an approval:")
calls.clear()
replies[:] = [
    {"message": {"content": "", "thinking": THINKING}, "done_reason": "length"},
    {"message": {"content": ""}, "done_reason": "length"},
]
urllib.request.urlopen = _fake_urlopen
try:
    out3, why3 = pf.ask("the work order", "qwen3.8:27b-mtp-bf16", num_predict=12000,
                  timeout=600)
except Exception as e:                                       # noqa: BLE001
    out3, why3 = "", f"RAISED {type(e).__name__}"
finally:
    urllib.request.urlopen = orig_urlopen
check("it tried twice and stopped there", len(calls), 2)
check("an empty salvage still reports length, so decide() refuses it",
      why3, "length")

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
