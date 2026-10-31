#!/usr/bin/env python3
"""The contract for scripts/triage.py.

Written BEFORE the implementation and RED against the stub. `ask` is injected everywhere, so
none of this calls a model: a suite that needs a 55 GB model loaded is a suite nobody runs.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import triage                                                # noqa: E402

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


F = [
    {"kind": "unknown", "key": "done_reason=lease_held", "at": "2026-09-10T01:00:00Z",
     "evidence": "done_reason lease_held", "source": "workspace/agent-runs.jsonl"},
    {"kind": "unknown", "key": "done_reason=lease_held", "at": "2026-09-10T02:00:00Z",
     "evidence": "done_reason lease_held", "source": "workspace/agent-runs.jsonl"},
    {"kind": "unknown", "key": "done_reason=lease_held", "at": "2026-09-10T03:00:00Z",
     "evidence": "done_reason lease_held", "source": "workspace/agent-runs.jsonl"},
    {"kind": "unknown", "key": "exit=141", "at": "2026-09-09T23:00:00Z",
     "evidence": "PUSH_EXIT=141", "source": "push.log"},
]

print("A QUEUE THAT GROWS WITH THE LOG IS THE 42-ITEM QUEUE AGAIN.")
print("Unknowns are deduplicated BY SHAPE, not listed per occurrence:")
s = triage.shapes(F)
check("four findings become two shapes", len(s), 2)
by = {x["key"]: x for x in s}
check("and the repeat carries its count", by["done_reason=lease_held"]["count"], 3)
check("with the first time it was seen", by["done_reason=lease_held"]["first_seen"],
      "2026-09-10T01:00:00Z")
check("and the last", by["done_reason=lease_held"]["last_seen"], "2026-09-10T03:00:00Z")
check("one example is kept, whole", by["exit=141"]["example"]["evidence"], "PUSH_EXIT=141")

print("\nIT PROPOSES. IT DOES NOT DECIDE. A benign classification silences a")
print("signal for good, and the model does not get to grant its own silence:")
calls = []


def ask_benign(prompt):
    calls.append(prompt)
    return json.dumps({"disposition": "benign", "why": "the interlock working as designed"})


out = triage.triage(s[:1], ask=ask_benign, limit=10)
check("it returns proposals", isinstance(out, list), True)
check("marked as a proposal, not a decision", out[0]["disposition"], "benign")
check("attributed", out[0]["proposed_by"] is not None, True)
check("and it did NOT write an allowlist",
      hasattr(triage, "allow") or hasattr(triage, "classify"), False)

print("\nTHE MODEL'S EVIDENCE MUST REACH IT. A triage on a shape the model")
print("cannot see is the brief-as-a-path failure of 2026-09-10 all over again:")
check("the evidence is in the prompt", "PUSH_EXIT=141" in " ".join(calls) or
      "lease_held" in " ".join(calls), True)

print("\nTHE LIMIT IS REAL. Unbounded triage makes every run pay the whole backlog:")
many = [{"kind": "unknown", "key": f"k{i}", "at": "2026-09-10T00:00:00Z",
         "evidence": f"e{i}", "source": "s"} for i in range(50)]
out = triage.triage(triage.shapes(many), ask=ask_benign, limit=3)
check("at most `limit` shapes are asked about", len(out), 3)

print("\nA MODEL REPLY IT CANNOT PARSE IS NOT A CLASSIFICATION.")
print("Fail closed: an unreadable answer proposes nothing (the preflight lesson):")
out = triage.triage(s[:1], ask=lambda p: "I think it's probably fine", limit=10)
check("an unparseable reply yields no proposal", out, [])
out = triage.triage(s[:1], ask=lambda p: json.dumps({"disposition": "whatever", "why": "x"}),
                    limit=10)
check("a disposition outside the allowed set yields no proposal", out, [])
out = triage.triage(s[:1], ask=lambda p: "", limit=10)
check("an empty reply yields no proposal", out, [])

print("\nNOTHING TO TRIAGE IS NOT A FAILURE, and must not look like one:")
check("no unknowns means no proposals", triage.triage([], ask=ask_benign, limit=10), [])
check("and no shapes", triage.shapes([]), [])

print("\nTHE RUN MUST LEAVE A TIMESTAMP. session_start reads it to say whether")
print("the 02:00 timer actually fired -- the timer is not trusted to report itself:")
d = Path(tempfile.mkdtemp())
triage.record_state(d / "state.json", proposed=2, pending=7)
st = json.loads((d / "state.json").read_text())
check("it records when it ran", isinstance(st.get("last_run"), str), True)
check("what it proposed", st.get("proposed"), 2)
check("and what is still waiting", st.get("pending"), 7)

print(f"\n  {PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
