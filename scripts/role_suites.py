#!/usr/bin/env python3
"""After a Models-tab save, run the suites that read the live role store, and record the result (t657).

    python3 scripts/role_suites.py           # run them now; writes workspace/run-logs/role-suites.json
    python3 scripts/role_suites.py --show    # print the last result

WHY (2026-10-02): three suites copied the LIVE role store into their fixtures and went red when Jas edited the
Models tab -- test_role_gate went red at 23:14Z and, through the red-on-main hold, stopped every build until it
was pinned by hand. role_store.save() starts this in the background after every save of the live store, so the
click is not slowed; the Models tab shows a red line naming the suite if one breaks, so the cause is seen at the
click and not at the next build. A run already in progress is not doubled (flock).
"""
import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESULT = REPO / "workspace" / "run-logs" / "role-suites.json"
LOCK = REPO / "workspace" / "run-logs" / "role-suites.lock"
# The suites that read projects/local-inference/models/ROLES.json (grep, 2026-10-05), plus task routing.
SUITES = ("test_role_gate.py", "test_roles_pages.py", "test_task_routing.py", "test_assigned_worker.py",
          "test_skill_change.py", "test_senior_station.py", "test_hold_menu.py", "test_role_store.py")
TIMEOUT_S = 600


def run():
    results = {}
    for s in SUITES:
        p = REPO / "scripts" / "tests" / s
        if not p.is_file():
            results[s] = "missing"
            continue
        try:
            r = subprocess.run([sys.executable, str(p)], cwd=str(REPO), capture_output=True, text=True,
                               timeout=TIMEOUT_S, env={**__import__("os").environ, "HIVEMIND_NO_ROLE_SUITES": "1"})
            results[s] = r.returncode
        except subprocess.TimeoutExpired:
            results[s] = "timeout"
    red = [s for s, rc in results.items() if rc != 0]
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "results": results, "red": red}
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    tmp = RESULT.with_suffix(".tmp")
    tmp.write_text(json.dumps(row, indent=1), encoding="utf-8")
    tmp.replace(RESULT)
    return row


def last():
    try:
        return json.loads(RESULT.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        return {"red": [], "error": f"{RESULT.name} unreadable: {e}"}


def main(argv):
    if "--show" in argv:
        print(json.dumps(last(), indent=1))
        return 0
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("role_suites: a run is already in progress")
            return 0
        row = run()
    print(f"role_suites: {len(row['red'])} red of {len(row['results'])}: {', '.join(row['red']) or 'none'}")
    return 1 if row["red"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
