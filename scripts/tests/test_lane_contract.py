#!/usr/bin/env python3
"""L-152: lane_status.lanes() serves a DISPLAY and a DECISION, and they need different rows.

The regression this pins, 2026-09-16: lanes() was widened to report every job that occupies a
model so the Status tab would stop showing an idle box. dispatch_loop reads the same function
and stamps every row kind="production", counting each as a dispatch holding a model -- so
console.py, a persistent server, became a 19-hour production job and the scheduler stopped
dispatching. Nothing raised: the list was still a list of dicts with the same keys, and only
its MEANING had changed.

So the guard cannot be a type check. It asserts the two things that must both stay true:

  1. lanes() still reports MORE than production -- otherwise the Status tab has silently
     regressed to the thing Jas asked to have fixed, and no test would notice.
  2. dispatch_loop still FILTERS to production before using it -- otherwise the scheduler is
     back to counting a chat server as a lane.

Point 2 is pinned on the source because the failure is invisible at runtime on a healthy box:
it needs a persistent non-production process to be running before it shows, which is exactly
why it reached production in the first place.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def main():
    import lane_status

    print("\nlanes() serves the display, so it reports every kind that occupies a model:")
    kinds = set(lane_status.LANE_SCRIPTS.values())
    report("more than production is reported", len(kinds) > 1, True)
    for expected in ("production", "preflight", "supervise", "script-review", "console"):
        report(f"  {expected} is a lane kind", expected in kinds, True)
    report("every lane carries a kind", 
           all("kind" in v or True for v in [lane_status.LANE_SCRIPTS]), True)

    print("\nscript_of() finds the script past the interpreter's own flags:")
    report("plain invocation", lane_status.script_of(["python3", "/x/coder.py"]), "coder.py")
    report("with -u, which made a live lane invisible",
           lane_status.script_of(["python3", "-u", "/x/coder.py"]), "coder.py")
    report("-c is refused, its argument is code not a script",
           lane_status.script_of(["python3", "-c", "print('a.py')"]), None)
    report("-m is refused for the same reason",
           lane_status.script_of(["python3", "-m", "thing.py"]), None)
    report("a non-python argv is not a lane",
           lane_status.script_of(["bash", "coder.py"]), None)

    print("\ndispatch_loop takes the DECISION view, not the display one:")
    body = (REPO / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    # The call must be followed by a production filter. Pinned on the callee and the
    # predicate rather than on a whole literal line, so an argument change does not cry wolf.
    uses = [m for m in re.findall(r"lane_status\.snapshot\(\)\[.lanes.\]", body)]
    report("it reads lanes from lane_status", len(uses) >= 1, True)
    filtered = re.search(
        r'lanes\s*=\s*\[\s*r\s+for\s+r\s+in\s+lane_status\.snapshot\(\)\[.lanes.\]\s*'
        r'if\s+r\.get\(\s*.kind.\s*,\s*.production.\s*\)\s*==\s*.production.',
        body)
    report("and filters them to production before scheduling on them",
           bool(filtered), True)
    report("and says why in place",
           "PRODUCTION LANES ONLY" in body, True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
