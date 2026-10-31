#!/usr/bin/env python3
"""stale_assignments.decide() moves only what the current routing rules would not assign (2026-10-19)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import stale_assignments as S  # noqa: E402

failed = 0


def check(name, got, want):
    global failed
    ok = got == want
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def note(who, text):
    return f"\n**2026-10-19 · {who}** — {text}\n"


J = {"assigned": "jas", "status": "queued"}
C = {"assigned": "claude", "status": "queued"}
check("Jas: an automated finding goes to production",
      (S.decide({**J, "source": "session-rca"}, "") or (None,))[0], "production")
check("Jas: a harness finding goes to Claude", (S.decide({**J, "source": "supervisor"}, "") or (None,))[0], "technical")   # tier (t9554)
check("Jas: his own request stays", S.decide({**J, "source": "workspace/app-feedback x"}, ""), None)
check("Jas: anything he has written on stays",
      S.decide({**J, "source": "session-rca"}, note("jas", "keep this")), None)
check("Claude: red on main goes to production", (S.decide({**C, "source": "x"}, note(
    "dispatch-loop", "Assigned to claude by the scheduler: chunk p1-t1 is waiting on claude. HELD: red on main")) or (None,))[0],
    "production")
check("Claude: a first objection goes to the scoper", (S.decide({**C, "source": "x"}, note(
    "dispatch-loop", "Assigned to claude by the scheduler: chunk p1-t1 is waiting on claude. HELD FOR CLAUDE: "
                     "Manager objected to this brief")) or (None,))[0], "rework")   # tier (t9554)
check("Claude: a second identical failure stays", S.decide({**C, "source": "session-rca"}, note(
    "dispatch-loop", "Assigned to claude by the scheduler: the SECOND identical failure")), None)
check("Claude: a harness park stays", S.decide({**C, "source": "session-rca"}, note(
    "dispatch-loop", "Assigned to claude by the scheduler: chunk p1 is waiting on claude. PARKED: BLOCKED BY A HARNESS BUG")), None)
check("done tasks are never moved", S.decide({**J, "status": "done", "source": "session-rca"}, ""), None)
print("FAILED" if failed else "ok")
sys.exit(1 if failed else 0)
