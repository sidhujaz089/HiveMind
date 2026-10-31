"""t1090 -- the Senior station is a node on the chart of record (FLOW.json services[]).

Red on main: FLOW.json's `services[]` has 13 entries (read 2026-10-13) and none carries unit
`ollama-station2.service`, while SERVING.json:27 says the Senior role's preflight/scoping lane
runs on that very unit and SERVERS.json:76-82 declares that server at http://127.0.0.1:11435.
The node is added by t1090's second checklist item, applied through scripts/flow_change.py's
flow-edit path so the edit is lint-checked and logged. This suite is the fixture that makes
'added' mean 'on the chart' -- and it goes red again if the node is ever deleted, which is the
decay test workspace/rca/a97157fc7a67.md asks for.

No live state is touched: the two repo files are READ, read-only. Nothing is written, no model is
called, no server is contacted, no process is left running. Every check is a plain assertion on
parsed JSON, so a missing node reads as a FAIL and not a crash.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLOW = ROOT / "projects" / "local-inference" / "models" / "FLOW.json"
SERVING = ROOT / "projects" / "local-inference" / "models" / "SERVING.json"
UNIT = "ollama-station2.service"

ok = 0
fail = 0


def check(name, cond):
    global ok, fail
    print("  [%s]   %s" % ("ok" if cond else "FAIL", name))
    if cond:
        ok += 1
    else:
        fail += 1


def load(path):
    """Parse a repo JSON file. A parse failure is a FAIL, not a traceback: an oracle that crashes
    for a reason that is not the missing change is the oracle-crash class."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        check("%s parses as JSON (%s)" % (path.name, e), False)
        return None


def main():
    flow = load(FLOW)
    serving = load(SERVING)
    if not isinstance(flow, dict) or not isinstance(serving, dict):
        print("%d passed, %d failed" % (ok, fail))
        return 1

    services = flow.get("services")
    check("FLOW.json carries a services list", isinstance(services, list))
    entries = [s for s in (services if isinstance(services, list) else []) if isinstance(s, dict)]

    ours = [s for s in entries if str(s.get("unit") or "") == UNIT]
    check("the chart's services[] declares the Senior station's unit %s -- the station, not a "
          "role label (the finding verbatim: the role label is present, the station is not)"
          % UNIT, len(ours) >= 1)
    check("  exactly one node carries it (no duplicate service nodes for one server)",
          len(ours) == 1)

    if not ours:
        for name in ("  the node carries a non-empty id, name and unit, as all 13 live entries do",
                     "  its name names the STATION (contains 'station'), so it reads as a server node",
                     "  its name links the role it serves (contains 'senior'), so the chart says "
                     "which role's preflight/scoping runs there",
                     "  its unit is spelled exactly %s" % UNIT):
            check(name, False)
    else:
        s = ours[0]
        check("  the node carries a non-empty id, name and unit, as all 13 live entries do",
              all(isinstance(s.get(k), str) and s.get(k).strip() for k in ("id", "name", "unit")))
        label = str(s.get("name") or "")
        check("  its name names the STATION (contains 'station'), so it reads as a server node",
              "station" in label.lower())
        check("  its name links the role it serves (contains 'senior'), so the chart says which "
              "role's preflight/scoping runs there", "senior" in label.lower())
        check("  its unit is spelled exactly %s" % UNIT, s.get("unit") == UNIT)

    roles = serving.get("roles")
    senior = roles.get("senior") if isinstance(roles, dict) else None
    check("SERVING.json declares the senior role (the station's owner)", isinstance(senior, dict))
    if isinstance(senior, dict):
        text = " ".join(str(v) for v in senior.values() if isinstance(v, str))
        check("  the role's own text names the same unit %s, so the pair is readable in both "
              "directions" % UNIT, UNIT in text)
        check("  the role carries its own lease (what makes it a station; SERVING.json:28 "
              "_what_endpoint records that endpoint is the server URL, not the unit)",
              bool(str(senior.get("lease") or "").strip()))

    print("%d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
