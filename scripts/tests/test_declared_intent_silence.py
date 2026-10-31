#!/usr/bin/env python3
"""t267. The silence check: a session that did work and left no task id is a
finding, in whatever words its intention was phrased.

WHY THIS EXISTS. `findings()` is a blacklist of phrasings (check_declared_intent.py,
the INTENT regex) and a phrasing nobody listed is invisible -- "That can wait
until after this lands" passes clean today. planning-for-sustainment, the
inversion section, gives the fallback when the good set cannot be enumerated:
a blacklist PLUS an explicit unknown bucket somebody dispositions, and the
bucket going permanently quiet is itself a finding, not good news. This suite
is that bucket, inverted onto the session: work done and zero task ids is a
finding regardless of the words.

G10 is why case 3 is pinned hard: a check that fires on a conversational
session that did nothing gets disabled, and a disabled check is worth less
than none.
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

    # 1. THE INVERSION -- words the phrasing library does not have. The assertion
    #    that the regex MISSES it comes first, or the case proves nothing about
    #    the inversion.
    unlisted = ["That can wait until after this lands."]
    check("precondition: the phrasing regex misses the unlisted commitment",
          cdi.findings(unlisted) == [], str(cdi.findings(unlisted)))
    check("work done with zero task ids is a silence finding",
          cdi.silence_finding({"commits": 1, "task_files": 0, "task_ids": 0}) is not None)
    check("task files touched with zero ids is also a finding",
          cdi.silence_finding({"commits": 0, "task_files": 2, "task_ids": 0}) is not None)

    # 2. ADDRESSABLE -- the work left a handle Jas can hold on his phone.
    check("commits plus one referenced id is silent",
          cdi.silence_finding({"commits": 2, "task_files": 0, "task_ids": 1}) is None)
    check("a task id is enough, even with no commits",
          cdi.silence_finding({"commits": 0, "task_files": 1, "task_ids": 3}) is None)

    # 3. G10 -- a conversational session that did nothing is not a finding; a check
    #    that fires there gets disabled, and a disabled check is worth less than none.
    check("no work and no ids is silent",
          cdi.silence_finding({"commits": 0, "task_files": 0, "task_ids": 0}) is None)

    # 4. DECAY -- the two halves are reported SEPARATELY, or the phrasing half can
    #    rot to zero and the total still read healthy.
    sf = cdi.silence_finding({"commits": 1, "task_files": 0, "task_ids": 0})
    live = cdi.report(["a bare declaration"], sf)
    lines = live.split("\n")
    check("the phrasing count is reported on its own line",
          any("1" in l and "phrasing" in l.lower() for l in lines))
    check("the silence finding is reported on its own line",
          any("silence" in l.lower() for l in lines))
    rotted = cdi.report([], sf)
    rlines = rotted.split("\n")
    check("phrasing half at zero still prints a count, separate from silence",
          any("0" in l and "phrasing" in l.lower() for l in rlines)
          and any("silence" in l.lower() for l in rlines))

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
