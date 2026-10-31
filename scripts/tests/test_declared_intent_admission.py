#!/usr/bin/env python3
"""t674: ADMISSIONS of uncompleted work are a finding; a handle in the same sentence turns it off.

The 2026-09-12 session deferred the hourly check-in in words the existing INTENT/OFFER blacklist
does not cover -- "isn't wired", "haven't started", "still outstanding", "checking manually",
"not yet implemented" -- so 6/7 of its real deferral lines were MISSED on verbatim replay
(RCA 7af5a8ef54a7). This is the decay test: RED on main because check_declared_intent has no
ADMISSION pattern, GREEN once the ADMISSION regex is added and wired into the findings() gate.

The silence half matters as much as the catch: an admission carrying a task id (ADDRESSED) or a
declined: line (DECLINED) in the SAME sentence must go silent, or the WARN becomes noise and dies.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import check_declared_intent as cdi


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'BAD'}]    {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(f"{label} {detail}")

    # 1. THE THING IT IS FOR -- bare admissions of uncompleted work, no handle. Each MUST be
    #    exactly one finding. On main findings() has no ADMISSION pattern, so each returns []
    #    and these five asserts are the RED (none of the five is an INTENT or OFFER match).
    bare = ["The hourly check-in harness isn't wired yet.",
            "I've been checking manually since the timer went quiet.",
            "I haven't started that part of the rebuild.",
            "The timer beat ledger is still outstanding.",
            "The hourly check-in is not yet implemented."]
    for s in bare:
        check(f"admission caught: {s[:44]}", len(cdi.findings([s])) == 1, str(cdi.findings([s])))

    # 2. THE HALF THAT MATTERS MORE -- a handle in the SAME sentence turns it off. ADDRESSED
    #    (a task id) or DECLINED (a declined: line) must silence the admission.
    handled = ["The hourly check-in harness isn't wired yet, tracked on t674.",
               "I haven't started that; declined: waiting on Jas's word.",
               "The beat ledger is still outstanding, filed as t278."]
    for s in handled:
        check(f"admission + handle silent: {s[:44]}", cdi.findings([s]) == [],
              str(cdi.findings([s])))

    # 3. DECAY GUARD -- ordinary prose must stay silent, or ADMISSION over-catches and the
    #    WARN dies. This line contains none of the ADMISSION phrases.
    prose = ["The guard drafts a skeleton and then blocks while it still carries placeholders."]
    check("ordinary prose does not trip ADMISSION", cdi.findings(prose) == [],
          str(cdi.findings(prose)))

    print()
    if fails:
        print(f"FAIL: {len(fails)} problem(s)")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
