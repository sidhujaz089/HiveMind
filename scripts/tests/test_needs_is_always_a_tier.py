#!/usr/bin/env python3
"""needs: is always a tier, whoever writes it (Claude, 2026-10-29; t9540 Part A follow-up).

After the tier migration, ten tasks were back on needs 'claude' or 'manager' within hours: escalate()
returns a ROLE and assigned_worker writes the role a model's OUTCOME names. taskstore.set_meta is the
one gate every writer passes, so it translates through role_store.as_tier. Red first: as_tier does not
exist on main.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import role_store as rs  # noqa: E402

FAILED = 0


def check(name, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


D = {"roles": {"boss": {"kind": "person", "escalations": ["vision"]},
               "fixer": {"kind": "person", "escalations": ["technical"]},
               "helper": {"kind": "agent", "escalations": []},
               "jas": {"kind": "person", "escalations": []},
               "lead": {"kind": "model", "escalations": ["rework"]},
               "worker": {"kind": "model", "escalations": []}}}
if not hasattr(rs, "as_tier"):
    print("  BAD  role_store has no as_tier()")
    sys.exit(1)
for v in ("vision", "technical", "rework", "production"):
    check(f"a tier stays itself: {v}", rs.as_tier(v, D), v)
check("blank is production", rs.as_tier("", D), "production")
check("'model' is production", rs.as_tier("model", D), "production")
check("a role names the class it HOLDS (fixer -> technical)", rs.as_tier("fixer", D), "technical")
check("a renamed holder still resolves (boss -> vision)", rs.as_tier("boss", D), "vision")
check("a model role holding rework -> rework", rs.as_tier("lead", D), "rework")
check("a model role holding nothing -> rework", rs.as_tier("worker", D), "rework")
check("an agent holding nothing -> technical", rs.as_tier("helper", D), "technical")
check("jas holding nothing -> vision", rs.as_tier("jas", D), "vision")
check("an unknown word is left visible, not guessed", rs.as_tier("nobody", D), "nobody")
src = (ROOT / "projects" / "app-repo" / "taskstore.py").read_text(encoding="utf-8")
body = src[src.index("def set_meta("):src.index("def _wake_date(")]
check("set_meta passes every needs write through as_tier", "role_store.as_tier(meta[k])" in body, True)
print("test_needs_is_always_a_tier:", "ok" if not FAILED else "FAILED")
sys.exit(1 if FAILED else 0)
