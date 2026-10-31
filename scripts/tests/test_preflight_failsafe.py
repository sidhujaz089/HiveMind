#!/usr/bin/env python3
"""preflight_brief.py must FAIL CLOSED on a verdict it does not recognise.

FOUND BY THE REVIEW PANEL, 2026-09-10, independently by four of eight reviewers.

The hole: the stop decision tests for the presence of strings in the reply. A model that
answers `VERDICT: PARTIALLY SATISFIABLE` does not contain "NOT SATISFIABLE" as the panel's
allowed token, may list nothing under MISSING, and therefore falls through to exit 0 --
APPROVAL. An unrecognised shape reads as permission.

Why that is the worst possible default here: this script exists to stop a brief that cannot be
met honestly. A gate whose unknown-input behaviour is "allow" is not a gate. Gemini put it as
the general rule -- an enforcement point that meets an anomalous state, an unmapped string or a
timeout must block by default; fail-open belongs in availability-critical systems, never in an
authorization or quality gate.

Meta gave the exact shape of the fix, and it is the shape this suite pins: parse the VALUE on
the VERDICT line and require MEMBERSHIP IN AN ALLOWED SET. Not substring presence anywhere in
the reply -- a model that writes "this is NOT SATISFIABLE in my view" in its prose must not be
able to flip the decision from inside the WHY paragraph.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import preflight_brief                                       # noqa: E402

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


# decide(reply_text) -> exit code. 0 only for an explicit, recognised approval.
D = getattr(preflight_brief, "decide", None)
if not callable(D):
    print("  [BAD]  preflight_brief.decide(reply) does not exist.")
    print("         Required: decide(reply_text) -> int, the exit code, so the stop")
    print("         rule can be tested without calling a model.")
    sys.exit(1)

print("THE ONLY TWO RECOGNISED VERDICTS:")
check("SATISFIABLE with nothing missing is approval",
      D("VERDICT: SATISFIABLE\nWHY: it is fine.\nMISSING: nothing"), 0)
check("NOT SATISFIABLE blocks",
      D("VERDICT: NOT SATISFIABLE\nWHY: the suite does not exist.\nMISSING:\n- the suite"), 2)

print("\nANYTHING ELSE MUST BLOCK. These are the shapes that reached exit 0:")
check("PARTIALLY SATISFIABLE blocks",
      D("VERDICT: PARTIALLY SATISFIABLE\nWHY: mostly.\nMISSING: nothing"), 2)
check("MOSTLY SATISFIABLE blocks",
      D("VERDICT: MOSTLY SATISFIABLE\nWHY: ok.\nMISSING: nothing"), 2)
check("a missing VERDICT line blocks",
      D("I think this brief is fine and you should go ahead."), 2)
check("an empty reply blocks -- a truncated review is NOT an approval",
      D(""), 2)
check("a reply that is only reasoning blocks",
      D("Let me think about whether this can be done...\n"), 2)

print("\nTHE VERDICT IS THE VALUE ON THE VERDICT LINE, not a substring anywhere.")
print("Prose must not be able to flip the decision:")
check("'SATISFIABLE' inside WHY does not approve an unrecognised verdict",
      D("VERDICT: UNCLEAR\nWHY: I would call it SATISFIABLE if the file existed.\n"
        "MISSING: nothing"), 2)
check("a NOT SATISFIABLE verdict is not rescued by prose saying otherwise",
      D("VERDICT: NOT SATISFIABLE\nWHY: honestly it is SATISFIABLE enough.\n"
        "MISSING: nothing"), 2)

print("\nTHE GAP LIST STILL OVERRIDES THE VERDICT. A reply that names something")
print("missing and says SATISFIABLE is the exact shape being prevented:")
check("SATISFIABLE with a gap listed still blocks",
      D("VERDICT: SATISFIABLE\nWHY: fine.\nMISSING:\n- scripts/tests/test_nope.py"), 2)

print("\nCASE AND WHITESPACE MUST NOT DECIDE SAFETY:")
check("lowercase is still recognised",
      D("verdict: satisfiable\nwhy: fine.\nmissing: nothing"), 0)
check("extra spacing is still recognised",
      D("VERDICT:    SATISFIABLE   \nWHY: fine.\nMISSING:  nothing  "), 0)

print(f"\n  {PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
