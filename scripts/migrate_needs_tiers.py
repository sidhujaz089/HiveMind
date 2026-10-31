#!/usr/bin/env python3
"""ONE-TIME: fold `assigned:` into `needs:` and make `needs:` the four tiers (t9540).

    python3 scripts/migrate_needs_tiers.py            # dry run: the counts, and the KPI before/after
    python3 scripts/migrate_needs_tiers.py --apply    # write it

WHY ONE FIELD. Jas, 2026-10-28: "No task should be linked to a role or model." A task carried BOTH an
`assigned:` lane and a `needs:` field, and they could disagree -- 611 `assigned: production`, 92 `claude`,
51 `manager` against a `needs:` that meant something else. Claude's decision 5: retire `assigned:`, keep
`needs:` as the ONE field, fold the old values into the four tiers, then delete the key.

THE MAPPING (Claude, 2026-10-28):
    jas            -> vision        a decision that is Jas's
    claude         -> technical     a frontier lane's judgement
    a role key     -> rework        the role at its Models-tab position
    production     -> production    the line
    empty or model -> production

THE GUARD THAT MATTERS (Claude's decision 1): "the migration prints the same window's rate before and
after in one run, and the build is refused if the rate moves." The rate is the 90% KPI, and this migration
must not buy it a better number by accident. Two things follow, and both are mechanical here:

  * the notes it writes use taskstore.set_tier, NOT set_meta, because set_meta writes "Reassigned X -> Y"
    which esc_re counts as a hand-off -- 700 of those would collapse the rate it is meant to measure;
  * before writing anything, the script asserts its own note wording does NOT match esc_re, and after
    writing it recomputes the rate and REFUSES (exit 1) if it moved.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
import taskstore as T  # noqa: E402

TIERS = ("vision", "technical", "rework", "production")


def tier_for(meta):
    """(tier, why) for one task's current fields. `needs` wins if it is already a tier."""
    needs = str(meta.get("needs") or "").strip().lower()
    assigned = str(meta.get("assigned") or "").strip().lower()
    for value, source in ((needs, "needs"), (assigned, "assigned")):
        if value in TIERS:
            return value, f"{source} already a tier"
        if value == "jas":
            return "vision", f"{source}=jas -> vision"
        if value in ("claude", "dsh", "dsh-harness"):
            return "technical", f"{source}={value} -> technical"
        if value in ("production", "model", ""):
            continue
        return "rework", f"{source}={value} (a role) -> rework"
    return "production", "production"


def rate(days=30):
    return T.escalation_rate(days=days)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="write it (default: report only)")
    ap.add_argument("--days", type=float, default=30.0, help="the KPI window (default 30)")
    a = ap.parse_args(argv)

    ids = T.read_queue() if hasattr(T, "read_queue") else []
    if not ids:
        print("  the queue is unreadable -- refusing to migrate blind", file=sys.stderr)
        return 2
    plan, counts = [], Counter()
    for tid in ids:
        meta, _body = T.find(tid)
        if not meta:
            continue
        tier, why = tier_for(meta)
        counts[why] += 1
        if str(meta.get("needs") or "") != tier:
            plan.append((tid, meta.get("needs"), tier))

    before = rate(a.days)
    print(f"  tasks read: {len(ids)} | would change: {len(plan)}")
    for why, n in counts.most_common():
        print(f"    {n:4d}  {why}")
    print(f"\n  KPI before ({a.days:g}d): {before['rate']:.1f}% "
          f"({before['done_clean']} of {before['created']})")

    # THE WORDING GUARD: the note this migration writes must not look like a hand-off. The pattern is
    # taskstore's ESC_RE -- the same one the KPI uses -- so the two cannot drift.
    sample = T.TIER_NOTE.format(old="claude", new="technical")
    if T.ESC_RE.search(sample):
        print(f"\n  REFUSED: the migration's own note matches the escalation pattern:\n    {sample}",
              file=sys.stderr)
        return 1
    print("  the migration's note wording does NOT match the escalation pattern (checked before writing)")

    if not a.apply:
        print("\n  dry run -- nothing written. Re-run with --apply.")
        return 0
    wrote = 0
    for tid, _old, tier in plan:
        if T.set_tier(tid, tier, why="t9540 tier migration, one field"):
            wrote += 1
    after = rate(a.days)
    print(f"\n  wrote {wrote} | KPI after ({a.days:g}d): {after['rate']:.1f}% "
          f"({after['done_clean']} of {after['created']})")
    if (after["done_clean"], after["created"]) != (before["done_clean"], before["created"]):
        print("  REFUSED AFTER THE FACT: the rate moved during the migration. The number is not\n"
              "  comparable and the migration must not be trusted until that is explained.", file=sys.stderr)
        return 1
    print("  the rate did not move: the migration changed the vocabulary, not the history.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
