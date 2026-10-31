#!/usr/bin/env python3
"""The session-start list and the stale-assignment sweep must read `needs:` as a TIER, not a role name.

t9540, the readers Claude named that I had not fixed yet. Two quiet regressions the migration caused:

  * session_start.claude_review_section filtered `assignee(needs) != "claude"`, so once needs held
    "technical" EVERY technical task vanished from the "waiting on Claude" list -- the list went empty
    without an error.
  * stale_assignments.decide compared `to == "jas"` and `to == "claude"`, neither of which can match a tier,
    so the weekly sweep decided NOTHING: silently inert rather than wrong, which is harder to notice.

Both now resolve the tier to its holders through role_store.tier_roles(), which accepts the old vocabulary
too (a role key names itself).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))


def main():
    import session_start as SS
    import stale_assignments as S
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    # --- session_start: is the task waiting on Claude?
    check("a TECHNICAL task is waiting on Claude", "claude" in SS._claude_holders({"needs": "technical"}),
          sorted(SS._claude_holders({"needs": "technical"})))
    check("a VISION task is not", "claude" not in SS._claude_holders({"needs": "vision"}),
          sorted(SS._claude_holders({"needs": "vision"})))
    check("a REWORK task is not", "claude" not in SS._claude_holders({"needs": "rework"}),
          sorted(SS._claude_holders({"needs": "rework"})))
    check("a legacy `claude` still is", "claude" in SS._claude_holders({"needs": "claude"}),
          sorted(SS._claude_holders({"needs": "claude"})))
    check("production never is", SS._claude_holders({"needs": "production"}) == set(),
          sorted(SS._claude_holders({"needs": "production"})))

    # --- stale_assignments: A TIER MUST DECIDE EXACTLY WHAT ITS OLD ROLE KEY DECIDED. Checking a specific
    # branch here would pin my guess about the rules rather than the property that matters, so each case is
    # run twice -- once with the tier the migration writes, once with the role key it replaced -- and the
    # two answers must be identical.
    cases = [
        ("vision", "jas", "", "session-review"),
        ("vision", "jas", "", "supervise"),
        ("technical", "claude",
         "**2026-10-28 · dispatch-loop** — by the scheduler: HELD: red on main: scripts/tests/test_x.py is red", ""),
        ("technical", "claude", "", "session-review"),
        ("rework", "manager", "", ""),
    ]
    for tier, role, body, src in cases:
        a = S.decide({"id": "tX", "status": "queued", "needs": tier, "source": src}, body, scoper="manager")
        b = S.decide({"id": "tX", "status": "queued", "needs": role, "source": src}, body, scoper="manager")
        check(f"the sweep decides the same for {tier!r} as for {role!r} (body={bool(body)}, source={src!r})",
              a == b, f"tier -> {a} | role -> {b}")
    check("  and it still DECIDES something rather than going inert",
          any(S.decide({"id": "tX", "status": "queued", "needs": t, "source": sr}, b, scoper="manager")
              for t, _r, b, sr in cases), "every case decided nothing")

    print("test_tier_readers_two:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
