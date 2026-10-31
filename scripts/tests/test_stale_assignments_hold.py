#!/usr/bin/env python3
"""Phase 0(d), 2026-10-27: the weekly stale-assignment sweep must not undo a deliberate hold.

WHY, FROM THE RECORD. t1448's notes, 2026-10-26:
    **2026-10-26 · dsh-harness** -- RE-HELD ... at claude-code's request. The bulk re-route undid a
    deliberate hold: this task loops because an assigned item's plan has no route to a builder
    (t1568, open). Hold restored until t1568 lands.
    **2026-10-26 · taskstore** -- Reassigned manager -> jas by taskstore.py: held for Jas (taskstore.py hold)
    **2026-10-26 · dsh-harness** -- HELD by dsh-harness -- parked for Jas to look at, not work to start.

So a sweep that reads only the assignment cannot tell "Jas has this because an old rule put it there"
from "Jas has this because someone parked it on purpose", and it moved the second kind -- twice. The
lane prompt for this effort puts it plainly: "stale_assignments.py must skip anything held by Jas and
anything whose last note says HOLD."

THE DISCRIMINATION IS NOT "HELD". The scheduler's own routing notes contain HELD and must STAY
re-routable -- test_stale_assignments.py pins both:
    "HELD: red on main"                      -> production
    "HELD FOR CLAUDE: Manager objected"       -> the scoper
Those are the sweep's whole purpose. What is skipped is a PARK: the words "held for Jas", "parked for
Jas", "RE-HELD", "held by <someone>", or a last note that says HOLD (the word, in those capitals) --
and a park that has since been released is not a park.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import stale_assignments as S  # noqa: E402

PASSED = FAILED = 0


def report(name, ok, detail=""):
    global PASSED, FAILED
    PASSED += bool(ok)
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        {detail}"))


def note(who, text, at="2026-10-19"):
    return f"\n**{at} · {who}** — {text}\n"


J = {"assigned": "jas", "status": "queued"}
C = {"assigned": "claude", "status": "queued"}

# The real text from t1448 and the real hold note taskstore.py writes.
PARKED = (note("taskstore", "Reassigned manager -> jas by taskstore.py: held for Jas (taskstore.py hold)")
          + note("dsh-harness", "HELD by dsh-harness -- parked for Jas to look at, not work to start."))
REHELD = (note("taskstore", "Reassigned manager -> jas by taskstore.py: held for Jas (taskstore.py hold)")
          + note("dsh-harness", "RE-HELD 2026-10-26T19:3xZ by dsh-harness, at claude-code's request. The "
                                "bulk re-route undid a deliberate hold: this task loops."))


def main():
    report("a task Jas holds, whose last note says HELD by a lane, is left alone",
           S.decide({**J, "source": "session-rca"}, PARKED) is None,
           S.decide({**J, "source": "session-rca"}, PARKED))
    report("  and a RE-HELD task with a later ordinary note is still left alone",
           S.decide({**J, "source": "supervisor"}, REHELD + note("claude", "looked at it")) is None,
           S.decide({**J, "source": "supervisor"}, REHELD + note("claude", "looked at it")))
    report("a last note that says HOLD (the word) stops the sweep",
           S.decide({**J, "source": "session-rca"}, note("claude-code", "HOLD -- do not sweep this")) is None,
           S.decide({**J, "source": "session-rca"}, note("claude-code", "HOLD -- do not sweep this")))
    report("a hold that was RELEASED does not stop it",
           (S.decide({**J, "source": "session-rca"},
                     PARKED + note("claude", "released -- back to the queue", at="2026-10-20")) or (None,))[0]
           == "production",
           S.decide({**J, "source": "session-rca"},
                    PARKED + note("claude", "released -- back to the queue", at="2026-10-20")))

    # UNCHANGED, and this is the half that keeps the sweep doing its job. Both cases are pinned by
    # test_stale_assignments.py too -- a change here is a change to the sweep's purpose.
    report("the scheduler's 'HELD: red on main' is still re-routed to production",
           (S.decide({**C, "source": "x"}, note(
               "dispatch-loop", "Assigned to claude by the scheduler: chunk p1-t1 is waiting on claude. "
                                "HELD: red on main")) or (None,))[0] == "production")
    report("the scheduler's 'HELD FOR CLAUDE' objection is still re-routed to the scoper",
           (S.decide({**C, "source": "x"}, note(
               "dispatch-loop", "Assigned to claude by the scheduler: chunk p1-t1 is waiting on claude. "
                                "HELD FOR CLAUDE: Manager objected to this brief")) or (None,))[0] == "rework")   # tier (t9554)
    report("a plain stale assignment on Jas still moves",
           (S.decide({**J, "source": "session-rca"}, "") or (None,))[0] == "production")

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
