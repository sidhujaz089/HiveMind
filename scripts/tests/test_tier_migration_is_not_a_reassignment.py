#!/usr/bin/env python3
"""A tier migration is not a reassignment -- and the check must still catch a real one.

2026-10-29: this check demanded a "Reassigned A -> B" note for ANY change of the assigned value. The one-time tier
migration (vision/technical/rework/production) rewrote 49 tasks with taskstore.set_tier(), whose note says in words
that it is NOT a reassignment -- because moving a task between tiers hands it to nobody. So the check failed on all
49, and because it runs in the commit hook, it blocked EVERY commit on main for everyone, not just the migration's
author. A check that stops the whole repository to report a non-event is worse than the gap it was written for.

The rule now distinguishes the two events. A sanctioned tier-migration note is an edge in the same chain; a change
with NO note at all still fails, and so does a real reassignment with no note.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import check_reassignment_notes as C
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    tier_note = "needs: production -> vision (tier migration; NOT a reassignment -- nothing was handed to anybody)"
    check("a tier-migration note chains the change", C.reaches(tier_note, "production", "vision") is True, tier_note)
    check("  and only where the note actually records that step",
          C.reaches(tier_note, "production", "technical") is False, "a step it did not record was accepted")
    check("a REAL reassignment note still chains",
          C.reaches("Reassigned claude -> vision by the scheduler: stale", "claude", "vision") is True, "")
    check("  no note at all still FAILS", C.reaches("", "production", "vision") is False, "")
    check("  and an unrelated note still FAILS",
          C.reaches("worked on it for an hour", "production", "vision") is False, "")

    src = (ROOT / "scripts" / "check_reassignment_notes.py").read_text(encoding="utf-8")
    check("the sanction is written down, not implied", "TIER MIGRATION IS NOT A REASSIGNMENT" in src, "")
    print("test_tier_migration_is_not_a_reassignment:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
