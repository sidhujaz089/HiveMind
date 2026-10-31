#!/usr/bin/env python3
"""manager_note reads the step an answer declares and routes the task: (b) is a decision above the Manager -> the technical seat (Claude) since 2026-10-19, which asks Jas only about what he wants."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import manager_note as M  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    cases = {"# t1\n**(c) SCOPE** -- the bridge": "c", "## t2 — step taken: **(b) NEEDS JAS**": "b",
             "**(a) ALREADY DONE as to the build**": "a", "(d) BLOCKED by t9": "d",
             "no step here at all": None}
    for text, want in cases.items():
        report(f"step of {text[:28]!r}", M.step_of(text), want)
    report("(b) routes to the technical seat, not straight to Jas (2026-10-19)", M.note_for("t2", "x.md", "(b) NEEDS JAS -- which policy")[1], "claude")
    report("(c) routes to Claude", M.note_for("t1", "x.md", "(c) SCOPE -- chunks")[1], "claude")
    report("no declared step still routes to Claude, and says so",
           M.note_for("t3", "x.md", "prose only")[0].startswith("Manager pass (queue top): **NO STEP"),
           True)
    report("a one-line promise of work decides nothing (not held for Jas)",
           M.decide("I'll do a quick verification pass on the line numbers, then write the answer."),
           None)
    # NO STEP GOES BACK TO SCOPING, NOT TO JAS (2026-10-19): 40 of his 242 Review tasks came this way.
    report("prose with no step goes back to scoping, not to Jas",
           (M.decide("Here is a long considered view with no step. " * 30) or ("", ""))[1], "")
    print(f"\n{PASSED} passed, {FAILED} failed")
    # A SCOPE THAT CANNOT BE BUILT GOES BACK TO SCOPING, NOT TO JAS (2026-10-08, t790).
    d = M.decide("## (c) SCOPE\nprose only, no chunks block\n")
    report("an unbuildable scope goes back to scoping (production), not to Jas", d[:2], ("queued", ""))
    report("  and says it cannot be built", d[2].startswith(M.UNBUILDABLE), True)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
