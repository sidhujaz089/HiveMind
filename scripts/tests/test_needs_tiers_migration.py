#!/usr/bin/env python3
"""The tier migration: the mapping, the one field, and the KPI that must not move for the wrong reason.

t9540 (Jas 2026-10-28: "No task should be linked to a role or model"; Claude's decisions 1 and 5). This
pins three things that are easy to get wrong in the same way:

  * the MAPPING, value by value, so a role key cannot quietly become "vision";
  * that the migration's own note does NOT look like a hand-off -- set_meta would write "Reassigned X -> Y",
    and 700 of those would collapse the very rate the migration has to leave alone;
  * that the KPI counts a TIER reassignment ("-> technical") as an escalation, which is the whole reason
    the pattern had to be extended.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))


def main():
    import taskstore as T
    import migrate_needs_tiers as M
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    cases = [
        ({"needs": "jas"}, "vision", "jas -> vision"),
        ({"needs": "claude"}, "technical", "claude -> technical"),
        ({"needs": "dsh-harness"}, "technical", "dsh-harness -> technical"),
        ({"needs": "manager"}, "rework", "a role -> rework"),
        ({"assigned": "manager"}, "rework", "assigned role -> rework"),
        ({"needs": "production"}, "production", "production stays production"),
        ({"needs": ""}, "production", "empty -> production"),
        ({"needs": "technical"}, "technical", "an existing tier is left alone"),
        ({"assigned": "production"}, "production", "assigned production -> production"),
    ]
    for meta, want, label in cases:
        got, why = M.tier_for(meta)
        check(f"mapping: {label}", got == want, f"got {got} ({why})")

    check("every tier the migration can write is one of the four", set(M.TIERS) ==
          {"vision", "technical", "rework", "production"}, str(M.TIERS))

    # THE NOTE MUST NOT READ AS A HAND-OFF.
    sample = T.TIER_NOTE.format(old="claude", new="technical")
    check("the migration's note does NOT match the escalation pattern", not T.ESC_RE.search(sample), sample)
    check("  but a real hand-off to a tier DOES (the KPI still sees one)", bool(
        T.ESC_RE.search("Reassigned production -> technical by taskstore: t9540")))
    check("  and the old wording is still counted", bool(T.ESC_RE.search("Reassigned production -> claude by x")))

    src = (ROOT / "scripts" / "migrate_needs_tiers.py").read_text(encoding="utf-8")
    check("the migration writes through set_tier, never set_meta", "set_tier(" in src and "set_meta(" not in src,
          "set_meta would write a Reassigned note")
    check("  and it refuses to apply when its own note matches",
          "REFUSED: the migration's own note matches" in src, "")
    check("  and it recomputes the rate after writing", "the rate did not move" in src, "")
    print("test_needs_tiers_migration:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
