#!/usr/bin/env python3
"""session_end must refuse to finish while the handover names an agreement with no task id.

WHY THIS EXISTS. workspace/rca/f15d4388b54e.md: on 2026-10-14 13:33 the assistant searched the
last two sessions' transcripts, the handovers, the tasks and the decisions log and found no
record of the multi-step scoping design Jas and a prior session had agreed (turn 184 of 287):
"That's a gap on our side: an agreement like that should have been in the handover." The design's
only durable artefact is t1177, which is the consequence (batching before scoping), not the
design. Cause: the agreement was never given a number, so the live context was its only carrier
and the transport that destroyed it (compaction, fork) took it. Prevention, rung 1 of that RCA:
the handover contract gains a required, machine-checkable section, and session_end.py -- the
recorder that already refuses to finish while the handover is unfilled -- refuses while that
section names an agreement with no handle.

WHAT THIS PINS, and the three ways a build can fake it.
1. The handle is per ROW, inside the agreements section. A build that greps the whole document
   silences everything; case 5 is the answer to that build -- the fixture handover below carries
   t1177 in the thread ledger, below the agreements section, on purpose.
2. A number is a record; the word 'task' is prose. Case 4 pins that.
3. 'None' is a legal disposition, so a session that agreed nothing can still close. A gate an
   empty day cannot satisfy is a gate bypassed within a week (planning-for-sustainment).
4. The function must be CALLED from main(). A build that defines it and never wires it passes
   cases 1-8 and enforces nothing -- the check-satisfied-by-dead-path class. Case 9 pins it on
   source text; the existing precedent is scripts/tests/test_declared_intent.py:70-72.

AUTHORSHIP: drafted by the manager lane; the bridge freezes it. The implementation is a separate
dispatch and may not edit this file (D-35).

RED ON MAIN FOR ONE REASON: session_end has no agreement_debts. Nothing else -- the import is
guarded so an import problem prints one FAIL line and exits 1 rather than crashing (oracle-crash).
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

SRC = REPO / "scripts" / "session_end.py"
HEAD = "## Agreements made this session"

try:
    import session_end as se
except Exception as e:
    print("FAIL: cannot import scripts/session_end.py: %s: %s" % (type(e).__name__, e))
    sys.exit(1)


def handover(rows, section=True):
    """A filled handover: no TODO markers, this session named, agreements section carrying rows.

    The thread ledger below the agreements section carries t1177 on purpose: case 5 asks whether
    a number that is in the DOCUMENT discharges a row that has no number. It must not.
    """
    body = "\n".join("- " + r for r in rows) if rows else ""
    sec = HEAD + "\n" + body + "\n\n" if section else ""
    return ("# Handover 2026-10-18\n\n"
            "Session: 4d79bb3a-cf72-4b21-abbc-8dbfc611e027\n\n"
            + sec +
            "## What was done\n- wrote the scoping table\n\n"
            "## Thread ledger\n"
            "| Thread | State | Artefact |\n|---|---|---|\n"
            "| scoping design | DECLARED-OPEN | t1177 |\n")


def main():
    fails = []

    def check(label, cond, detail=""):
        mark = "ok" if cond else "BAD"
        print("  [" + mark + "]    " + label + (("  " + detail) if detail else ""))
        if not cond:
            fails.append(label + " :: " + detail)

    if not hasattr(se, "agreement_debts"):
        print("FAIL: scripts/session_end.py defines no agreement_debts() -- that function IS the "
              "change (handover text -> list of findings)")
        return 1
    ad = se.agreement_debts

    # 1. THE THING IT IS FOR: t1177's own subject -- an agreement stated as prose, no number.
    out = ad(handover(["Batching runs as a task before scoping; intake pass, then link pass"]))
    check("an agreement with no task id is a finding", len(out) == 1, str(len(out)) + ": " + str(out))
    check("the finding quotes the agreement's own words",
          bool(out) and "Batching runs as a task before scoping" in out[0], str(out[:1]))

    # 2. A NUMBER DISCHARGES IT. Three real ids, distinct values, so one placeholder cannot do it.
    for tid in ("t1177", "t1445", "t265"):
        out = ad(handover(["Batching runs as a task before scoping " + tid]))
        check(tid + " in the row discharges it", out == [], str(out))

    # 3. owner + date is the second legal handle -- the RCA's own form.
    out = ad(handover(["Intake->Link->Scope is the staged shape, agreed with Jas 2026-10-14"]))
    check("owner + date discharges the agreement", out == [], str(out))

    # 4. THE WORD 'task' IS NOT A NUMBER. This is the sentence that ended up unrecorded.
    out = ad(handover(["Intake->Link->Scope is recorded as a task, title agreed"]))
    check("the word 'task' with no id is still a finding", len(out) == 1, str(out))

    # 5. THE HANDLE IS PER ROW, INSIDE THE SECTION. The fixture's thread ledger carries t1177
    #    below, so a build that greps the whole document reports 0 here and fails.
    out = ad(handover(["Batching runs as a task before scoping, intake then link"]))
    check("a tNNN in a different section does not discharge the row", len(out) == 1, str(out))

    # 6. TWO ROWS, ONE HANDLED: the unhandled one is named, by its own words.
    out = ad(handover(["Intake->Link->Scope is the staged shape, agreed with Jas 2026-10-14",
                       "batching runs as a task before scoping"]))
    check("two agreements, one numbered -> one finding", len(out) == 1, str(out))
    check("it names the unnumbered row, not the numbered one",
          bool(out) and "batching runs as a task before scoping" in out[0], str(out[:1]))

    # 7. AN EMPTY DAY CAN CLOSE: a legal disposition is silent, so the gate is not a nag.
    for row in ("None", "none", "None.", "No agreements made this session"):
        out = ad(handover([row]))
        check("legal empty-day row is silent: " + repr(row), out == [], str(out))

    # 8. NO SECTION AT ALL: a finding that names the heading, so the author learns the shape
    #    (draft-then-block: a gate must show the shape it demands).
    out = ad(handover([], section=False))
    check("a handover with no agreements section is a finding", len(out) == 1, str(out))
    check("it names the section the author must add",
          bool(out) and "Agreements" in out[0], str(out[:1]))

    # 9. THE FUNCTION IS WIRED, AND THE MESSAGE SAYS WHAT TO DO. session_end.py is one file, so
    #    a definition-never-called build is checkable here (case 1-8 pass, nothing is enforced).
    src = SRC.read_text(encoding="utf-8")
    n_calls = len(re.findall(r"\bagreement_debts\(", src))
    check("agreement_debts is CALLED, not only defined", n_calls >= 2,
          str(n_calls) + " occurrence(s) of 'agreement_debts(' in the source")
    check("the refusal tells the author the form required",
          "Agreements" in src and "tNNN" in src,
          "no 'Agreements'/'tNNN' in any text the gate prints")

    print()
    if fails:
        print("FAIL: " + str(len(fails)) + " problem(s)")
        for f in fails:
            print("  - " + f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
