#!/usr/bin/env python3
"""Escalations are edited on each ROLE, like its Responsibilities (t9540 R5; Jas 2026-10-29: "I don't see
the escalations in the role. I see it on the model tab still."). Drives the real Hub POST handler against
a COPY of the role store -- never the live ROLES.json.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import role_store as rs  # noqa: E402
import roles_pages as rp  # noqa: E402

FAILED = 0


def check(name, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    for f in ("ROLES.json", "SERVING.json"):
        src = rs.MODELS / f
        if src.exists():
            shutil.copy(src, td / f)
    saved = (rs.MODELS, rs.STORE, rs.LOG, rs.SERVING, rs.FLOW_WORKERS)
    rs.MODELS, rs.STORE, rs.LOG, rs.SERVING = td, td / "ROLES.json", td / "ROLES-CHANGELOG.jsonl", td / "SERVING.json"
    rs.FLOW_WORKERS = td / "FLOW-WORKERS.json"          # every save writes the projection too
    try:
        role = next(k for k, r in rs.load()["roles"].items() if r.get("kind") == "agent") \
            if any(r.get("kind") == "agent" for r in rs.load()["roles"].values()) else rs.rank()[-1]
        post = lambda **f: rp.handle_post(f"/models/{role}/escalations", lambda n: f.get(n, ""), f)  # noqa: E731
        check("the role page links to Escalations", "/escalations" in rp.role_page(role), True)
        post(op="add", cls="technical")
        check("ADD writes the role's own list", rs.load()["roles"][role].get("escalations"), ["technical"])
        check("  and the role now holds the class", role in rs.escalate_all("technical"), True)
        post(op="add", cls="rework")
        post(op="up", cls="rework")
        check("REORDER moves it up", rs.load()["roles"][role]["escalations"], ["rework", "technical"])
        post(op="remove", cls="rework")
        check("REMOVE drops it", rs.load()["roles"][role]["escalations"], ["technical"])
        check("the Models tab shows holders read-only, no position box",
              ("/models/escalation\"" in rp.index_page(), "Escalations" in rp.index_page()), (False, True))
        check("the escalations page renders the held class", "technical" in rp.escalations_page(role), True)
    finally:
        rs.MODELS, rs.STORE, rs.LOG, rs.SERVING, rs.FLOW_WORKERS = saved
print("test_role_escalations_editor:", "ok" if not FAILED else "FAILED")
sys.exit(1 if FAILED else 0)
