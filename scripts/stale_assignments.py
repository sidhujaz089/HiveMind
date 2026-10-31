#!/usr/bin/env python3
"""Return tasks whose assignment the CURRENT routing rules would not make (harness improvement #5).

    python3 scripts/stale_assignments.py            # dry run: what would move, and why
    python3 scripts/stale_assignments.py --apply    # move them (each with its Reassigned note)

WHY (Jas, 2026-10-19): rules improve, old assignments stay. On that day 242 tasks sat with Jas and 328
with Claude; most were there only because of a rule since changed -- an answer with no step went to
Jas until 38dc70da, a first brief defect went to Claude until t1071, a whole migration (263634fe)
defaulted unowned work to Jas. The sweep that night moved ~300 by hand. This is that sweep, weekly:
  * Jas's tasks from AUTOMATED sources (no question about what he wants, no note from him)
      -> production, or Claude for harness bugs;
  * Claude's tasks whose last scheduler routing is one the current rules no longer give Claude
    (red on main -> production; a FIRST objection / came-to-nothing / needs-scope -> the scoper);
  * Claude's automated findings with nothing that needs Claude (no harness park, no delivered
    build, no second identical failure) -> production.
Jas's own requests and anything he has written on are never moved.
"""
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "projects" / "app-repo"))
sys.path.insert(0, str(REPO / "scripts"))

HARNESS_SOURCES = {"supervisor", "q16-supervisor", "dsh-harness"}
FINDING_SOURCES = {"session-rca", "session_review", "session-review", "scope-quality", "hourly-checks"}
CLAUDE_REASONS = ("HARNESS BUG", "SECOND identical", "DELIVERED", "supervising, parked")
NOTE = re.compile(r"^\*\*\d{4}-\d\d-\d\d · ([^*]+)\*\* — (.*)$", re.M)

# A DELIBERATE PARK IS NOT A STALE ASSIGNMENT (2026-10-27, L2 Phase 0d). t1448's notes record what
# this sweep did without the distinction: "The bulk re-route undid a deliberate hold ... Hold
# restored until t1568 lands." The assignment field cannot tell an old rule from a person's decision,
# so the decisions are read out of the notes.
# THE WORDS MATTER, and they were taken from the notes on disk rather than invented:
#   taskstore.py's own hold  -> "held for Jas (taskstore.py hold)"
#   a lane parking it        -> "HELD by dsh-harness -- parked for Jas to look at, not work to start."
#   a second deliberation    -> "RE-HELD 2026-10-26T19:3xZ by dsh-harness, at claude-code's request."
# NOT matched, deliberately: the scheduler's own "HELD: red on main" and "HELD FOR CLAUDE: Manager
# objected" -- test_stale_assignments.py pins both as re-routable, and re-routing them is this
# sweep's whole purpose. A bare HELD would have swept them up with the parks.
PARK_RE = re.compile(r"held for Jas|parked for Jas|\bre-?held\b|\bheld by \w", re.I)
HOLD_WORD_RE = re.compile(r"\bHOLD\b")
RELEASED_RE = re.compile(r"\b(released|unheld|un-held|off hold)\b", re.I)


def parked(notes):
    """Why this task is deliberately parked, or "" when it is not. `notes` is [(who, text)] in order."""
    last = notes[-1][1] if notes else ""
    if HOLD_WORD_RE.search(last):
        return "its last note says HOLD"
    for n, (_who, text) in enumerate(reversed(notes)):
        if PARK_RE.search(text):
            later = [t for _w, t in notes[len(notes) - n:]]
            if not any(RELEASED_RE.search(t) for t in later):
                return "parked deliberately (a lane or Jas held it)"
    return ""


def decide(meta, body, scoper="rework"):
    """(new assignee, why) for a task the current rules would route elsewhere, else None. Pure."""
    import taskstore as ts
    if meta.get("status") in ("done", "cancelled"):
        return None
    to = ts.assignee(meta.get("needs", meta.get("assigned")))
    # A TIER IS A SET OF ROLES (t9540). With needs: holding rework / technical / vision, `to == "jas"` and
    # `to == "claude"` are both false for every task, so this sweep decided NOTHING -- it went quietly inert
    # the moment the migration ran. Resolving the tier to its holders keeps both vocabularies working.
    # BY TIER, NOT BY ROLE NAME (t9554): the tier a value means, whichever vocabulary wrote it.
    tier = ts.tier_of(to)
    notes = NOTE.findall(body or "")
    if parked(notes):
        return None                                   # a decision, not a stale rule
    if any(w.strip() == "jas" for w, _ in notes):
        return None                                   # he has written on it: his
    src = ((meta.get("source") or "").split() or [""])[0]
    sched = [t for w, t in notes if w.strip() == "dispatch-loop" and "by the scheduler" in t]
    last = sched[-1] if sched else ""
    if tier == "vision":
        if src in HARNESS_SOURCES:
            return "technical", f"automated harness finding ({src}) with no question for Jas"
        if src in FINDING_SOURCES:
            return "production", f"automated finding ({src}) with no question for Jas"
        return None
    if tier == "technical":
        if any(k in last for k in CLAUDE_REASONS):
            return None
        if "HELD: red on main" in last:
            return "production", "a red-on-main hold assigns nothing under the current rules"
        if "by the scheduler: chunk" in last and any(
                k in last for k in ("objected to this brief", "came to nothing", "NEEDS-SCOPE")):
            return scoper, "a first brief defect goes to the scoper under the current rules (t1071)"
        if src in FINDING_SOURCES:
            return "production", f"automated finding ({src}) with nothing that needs Claude"
    return None


def main(argv):
    import taskstore as ts
    import task_routing
    # The scoper is whoever holds the SCOPING responsibility; a re-scope is rework, so the tier is written.
    # (Was owner(d, "manager"): "manager" is not a job, so it always fell back to the literal.)
    scoper = "rework" if task_routing.who_does("scoping") != task_routing.UNKNOWN else task_routing.UNKNOWN
    apply = "--apply" in argv
    moves = []
    for tid, (meta, body, *_) in ts.load_all().items():
        d = decide(meta, body, scoper)
        if d:
            moves.append((tid, ts.assignee(meta.get("needs", meta.get("assigned"))), *d))
    for tid, old, new, why in moves:
        print(f"  {tid}: {old} -> {new}  ({why})")
        if apply:
            ts.set_meta(tid, needs=new, why=f"{why} -- weekly stale-assignment sweep", who="stale-assignments")
    print(f"  {len(moves)} stale assignment(s){' moved' if apply else ' (dry run)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
