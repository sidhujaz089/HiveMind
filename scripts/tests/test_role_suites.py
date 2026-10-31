#!/usr/bin/env python3
"""t657: a Models-tab save checks the role-store suites in the background, and the tab shows a red one.
Hermetic: temp result files and a recording Popen; no suite is really run and the live store is not saved."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import role_store as rs  # noqa: E402
import role_suites  # noqa: E402
import roles_pages  # noqa: E402

FAILS = []


def ck(name, cond, detail=""):
    print(f"  [{'ok' if cond else 'BAD'}]  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


started = []
real_popen = rs.subprocess.Popen
rs.subprocess.Popen = lambda cmd, **kw: started.append((cmd, kw))
was = os.environ.pop("HIVEMIND_NO_ROLE_SUITES", None)
try:
    rs._check_suites_later()
    ck("a save of the live store starts role_suites.py in the background",
       len(started) == 1 and started[0][0][-1].endswith("role_suites.py") and started[0][1].get("start_new_session"),
       started)
    os.environ["HIVEMIND_NO_ROLE_SUITES"] = "1"
    rs._check_suites_later()
    ck("inside the suites themselves it does not start again (no recursion)", len(started) == 1, started)
finally:
    rs.subprocess.Popen = real_popen
    os.environ.pop("HIVEMIND_NO_ROLE_SUITES", None)
    if was is not None:
        os.environ["HIVEMIND_NO_ROLE_SUITES"] = was

d = Path(tempfile.mkdtemp())
role_suites.RESULT = d / "role-suites.json"
ck("no result yet: no banner", roles_pages.suites_banner() == "")
role_suites.RESULT.write_text(json.dumps({"at": "2026-10-05T00:00:00Z", "results": {"test_role_gate.py": 1},
                                          "red": ["test_role_gate.py"]}))
b = roles_pages.suites_banner()
ck("a red suite shows a red line naming it and the changelog to undo from",
   "test_role_gate.py" in b and "RED" in b and "/models/changelog" in b, b)
role_suites.RESULT.write_text(json.dumps({"at": "x", "results": {"test_role_gate.py": 0}, "red": []}))
ck("all green: no banner", roles_pages.suites_banner() == "")
ck("every suite it runs exists", [s for s in role_suites.SUITES if not (ROOT / "scripts" / "tests" / s).is_file()] == [])

print("test_role_suites:", "FAILED" if FAILS else "ok")
sys.exit(1 if FAILS else 0)
