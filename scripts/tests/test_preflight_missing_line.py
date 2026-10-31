#!/usr/bin/env python3
"""preflight_brief.decide() reads MISSING from the MISSING line -- prose saying "missing:" is not a gap.

Four SATISFIABLE reviews were recorded stopped=True on 2026-09-23 (t287 twice, p19, p24): each
reply's prose contained "... is missing: <explanation>", decide() took the first "missing:"
anywhere as the gap list, and blocked an approval that ended "MISSING: nothing". The replies
below are the shapes from preflight-objections.jsonl.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import preflight_brief as pb  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


P24 = ("VERDICT: SATISFIABLE\nWHY: Every path exists. No dependency is missing: the import chain "
       "resolves entirely within the provided files.\nMISSING: nothing.")
GAP = "VERDICT: SATISFIABLE\nWHY: looks fine.\nMISSING: scripts/settings.py is not shown"
NO_MISSING_LINE = "VERDICT: SATISFIABLE\nWHY: all shown."
NOT_SAT = "VERDICT: NOT SATISFIABLE\nWHY: the import is forbidden.\nMISSING: nothing"


def main():
    report("p24's reply (prose 'missing:', line 'MISSING: nothing') is an approval", pb.decide(P24), 0)
    report("a real MISSING line still blocks", pb.decide(GAP), 2)
    report("no MISSING line at all is an approval", pb.decide(NO_MISSING_LINE), 0)
    report("NOT SATISFIABLE still blocks", pb.decide(NOT_SAT), 2)
    report("an empty reply still blocks", pb.decide(""), 2)
    report("'MISSING:' with the list on the next lines still blocks",
           pb.decide("VERDICT: SATISFIABLE\nWHY: fine.\nMISSING:\n- scripts/tests/test_nope.py"), 2)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
