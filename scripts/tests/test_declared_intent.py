#!/usr/bin/env python3
"""A bare declaration is found; the same declaration with a task id is not.

WHY BOTH HALVES. `check_declared_intent.py` is a WARN, and a WARN that fires on everything is
a WARN that gets scrolled past -- AGENTS.md rule 9, and `G10` in this repository's own terms.
So the half that matters is the SILENCE: giving an intention a task id must actually turn the
finding off, or the fixture teaches nothing and the next session goes back to prose.

The check exists because of Jas, 2026-09-11: "When you say 'I'll be checking', are you doing
something or declaring you will? This has come up before where you weren't doing something and
I thought you were." The first case below is his sentence, near enough verbatim.
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

    # 1. THE THING IT IS FOR -- the sentence Jas objected to.
    bare = ["One thing I'll be checking in its draft: session_end.py derives REPO_ROOT "
            "from __file__."]
    hits = cdi.findings(bare)
    check("a bare 'I'll be checking' is reported", len(hits) == 1, f"{len(hits)} finding(s)")

    # 2. THE HALF THAT MATTERS MORE -- a handle turns it off.
    handled = ["I'll check whether session_end.py derives REPO_ROOT from __file__; "
               "recorded on t265 so it cannot be lost."]
    check("the same sentence with a task id is silent", cdi.findings(handled) == [],
          str(cdi.findings(handled)))

    # 3. Done-in-this-turn is not a deferral, and must not be nagged.
    now = ["Let me check whether session_end.py has a clean seam before I answer."]
    check("work happening in the same turn is silent", cdi.findings(now) == [],
          str(cdi.findings(now)))

    # 4. Past tense is a report, not a promise. The whole point is to push sessions here.
    past = ["I checked session_end.py: REPO_ROOT is derived from __file__ at line 25."]
    check("a past-tense report is silent", cdi.findings(past) == [], str(cdi.findings(past)))

    # 5. OTHER PHRASINGS, or the check buys one idiom instead of the behaviour.
    for s in ["I will rewrite that assertion before freezing.",
              "Next I'll dispatch the implementation to coder.py.",
              "I'm going to fix the oracle first.",
              "My next step is to review the draft."]:
        check(f"caught: {s[:44]}", len(cdi.findings([s])) == 1)

    # 6. DECAY GUARD ON THE CHECK ITSELF. If the regex is ever widened until ordinary prose
    #    trips it, the WARN becomes noise and dies. Plain description must stay silent.
    prose = ["The guard drafts a skeleton and then blocks while it still carries placeholders.",
             "Q16 is drafting the oracle now, through ask_q16.py rather than coder.py.",
             "That number is a measurement at 11 skills, not a property."]
    check("ordinary description does not trip it", cdi.findings(prose) == [],
          str(cdi.findings(prose)))

    # 7. It must read the LIVE transcript, not the previous session's. Inverting L-148 is the
    #    one thing about this script that is easy to "simplify" into being wrong.
    # The CALL form, not the bare name -- the docstring names `previous_session` to explain
    # why it is not used, and asserting on the substring failed on its own explanation.
    src = (REPO / "scripts" / "check_declared_intent.py").read_text()
    check("live_turns does not call tr.previous_session", "tr.previous_session(" not in src)
    check("...and the module it must read is still imported", "import transcripts as tr" in src)

    # t497/t516/t527: offers and parks, verbatim from the session-review findings of 2026-09-28/29
    for s in ["Say the word and I'll set an explicit WEBUI_SECRET_KEY.",
              "I left both alone since deletion is yours to call.",
              "nothing in AGENTS.md or docs/ references them yet. Worth wiring in next session.",
              "If you want the narrow auto-merge, the rule I'd propose is: one file."]:
        check(f"offer caught: {s[:44]}", len(cdi.findings([s])) == 1, str(cdi.findings([s])))
    for s in ["If you want it, that is t559.",
              "Declined: deleting unmerged branches discards their commits.",
              "Not doing it now because the box is busy -- declined until Jas says."]:
        check(f"disposed offer silent: {s[:40]}", cdi.findings([s]) == [], str(cdi.findings([s])))

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
