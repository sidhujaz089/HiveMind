#!/usr/bin/env python3
"""The Review tab groups by TIER, in one order, and never twice.

t9540 (Jas 2026-10-28 + Claude's decision 2). Two things changed:
  * the sections are Needs Vision / Needs Technical Guidance / Needs Rework, in that order -- the old
    headings named a ROLE ("Assigned to Manager"), which is the vocabulary this work removes;
  * a task appears ONCE however many roles hold its tier, and a tier with two holders (Claude and DSH both
    technical) must not double the queue on screen.

The mapping is read TOLERANTLY because the storage migration (needs: <- assigned:) is a separate, signed
change: until it lands a task may still carry jas / claude / a role key, and the page must not mis-file it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))


def main():
    import feedback as F
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    roles = {"manager", "auditor", "coder", "embedder", "claude", "jas", "dsh"}
    cases = [
        ("jas", "vision"), ("vision", "vision"),
        ("claude", "technical"), ("dsh", "technical"), ("dsh-harness", "technical"),
        ("technical", "technical"),
        ("manager", "rework"), ("auditor", "rework"), ("rework", "rework"),
        ("", "production"), ("production", "production"), ("model", "production"),
        ("some-role-that-is-new", "rework"),
    ]
    for needs, want in cases:
        got = F.tier_of(needs, roles)
        check(f"mapping: {needs!r} -> {want}", got == want, f"got {got}")

    check("the three sections have the names Jas asked for",
          [F.TIER_TITLE[t] for t in F.TIER_ORDER] ==
          ["Needs Vision", "Needs Technical Guidance", "Needs Rework"], str(F.TIER_ORDER))
    check("  and vision comes first, as the order says", F.TIER_ORDER[0] == "vision", str(F.TIER_ORDER))

    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    check("the page groups by tier, not by assignee",
          "groups.setdefault(tier_of(m.get(\"needs\"), role_keys), []).append(m)" in src,
          "still grouping by assignee")
    check("  and NO 'Assigned to <role>' heading is built anywhere for tasks",
          "'<h2>Assigned to {esc(label)}" not in src, "the old heading is still there")
    check("  the sections render from TIER_TITLE", "TIER_TITLE.get(k," in src, "")
    print("test_review_tab_by_tier:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
