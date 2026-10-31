#!/usr/bin/env python3
"""Scheduled work describes itself on the Flow chart, including the jobs nobody has added yet (2026-10-30).

Jas: "Can you make sure all these cron events appear on the flow chart dynamically if they dont already? It needs to
catch the future ones automatically too."

Two things had to be true, and neither was:
  * the chart had NO machine-readable schedule block at all -- timers existed only as prose inside other nodes, and
    the old check passed when a unit's NAME appeared anywhere in that prose;
  * nothing regenerated it, so a timer added tomorrow would be invisible until a person noticed.

RED ON MAIN, in order of what the suite proves: flow_schedules did not exist, FLOW.json carried no `schedules`
block, and check_workspace's "Every standing process is on the Flow chart" compared prose.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import flow_schedules as fs          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


# --- the comparison -----------------------------------------------------------------------------
live = [
    {"id": "a.timer", "kind": "timer-user", "what": "A", "when": "*-*-* 05:30:00", "next": ""},
    {"id": "cron:b.py", "kind": "cron-user", "what": "python3 b.py", "when": "*/15 * * * *", "next": ""},
]
chart = {"schedules": [{"id": "a.timer"}]}
got = fs.problems(live, chart)
check("a scheduled job the chart does not carry is reported", [(i, w) for i, w in got] ==
      [("cron:b.py", "cron-user: */15 * * * *")], str(got))
check("and one it does carry is not", "a.timer" not in [i for i, _ in got], str(got))
check("a chart that carries everything reports nothing",
      fs.problems(live, {"schedules": [{"id": "a.timer"}, {"id": "cron:b.py"}]}) == [])

# the FUTURE job: one that exists now and did not when the chart was last written
later = live + [{"id": "cron:new.py", "kind": "cron-user", "what": "python3 new.py",
                 "when": "0 3 * * *", "next": ""}]
check("a job added after the chart was written is caught", [i for i, _ in fs.problems(later, chart)] ==
      ["cron:b.py", "cron:new.py"], str(fs.problems(later, chart)))

# --- the live chart -----------------------------------------------------------------------------
CHART = json.loads((HERE / "projects" / "local-inference" / "models" / "FLOW.json").read_text(encoding="utf-8"))
rows = CHART.get("schedules") or []
check("the live chart carries a schedules block", bool(rows), "run: python3 scripts/flow_schedules.py --sync")
check("every row says what it is and when it runs",
      all(r.get("id") and r.get("kind") and "when" in r for r in rows), str(rows[:1]))
check("the block is a PROJECTION of what is scheduled on this box",
      [r["id"] for r in rows] == [r["id"] for r in fs.snapshot()] or fs.problems() == [],
      "the chart's ids must be the live ids, or problems() reports the gap")

# --- the check reads the projection, not the prose ----------------------------------------------
src = (HERE / "scripts" / "check_workspace.py").read_text(encoding="utf-8")
check("check_workspace asks flow_schedules rather than matching prose",
      "import flow_schedules" in src and "flow_schedules.problems()" in src)
check("  and its failure says how to regenerate the projection",
      "flow_schedules.py --sync" in src)

# AND THE CHECK MUST ACTUALLY RUN ITS BRANCH. The first version of this wiring used `SCRIPTS`, which is not a name
# in check_workspace, so the check failed closed with "could not read the schedule projection: NameError" -- correct
# behaviour, found only by running the real check. This compiles the module and greps the call, so a name that does
# not exist there is visible here.
import py_compile
try:
    py_compile.compile(str(HERE / "scripts" / "check_workspace.py"), doraise=True)
    check("check_workspace still compiles", True)
except py_compile.PyCompileError as e:
    check("check_workspace still compiles", False, str(e)[-200:])
check("  and its projection call path is spelled with a name this module has",
      'sys.path.insert(0, str(self.repo / "scripts"))' in src, "SCRIPTS is not a module-level name there")

print("test_flow_schedules:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
