#!/usr/bin/env python3
"""t1594: "who works this stage" is DERIVED from ROLES.json, never a second copy in FLOW.json.

Measured 2026-10-25: of the chart's 10 worker_kind "role" nodes, FIVE carried a second copy of a fact
the role store already owned -- preflight, session_review and rca said "Senior" for a week after that
role was deleted, and console/assigned_items said "any role" and "the assigned model role". The store
changed and the chart did not; the strict foreign-key check reported the chart, and check_workspace hid
it by passing fk=False.

Jas: "Roles should not be hard coded into anything except for the Role profiles in the Role tab. That
is the source of truth and everything else points at it to make it dynamic."

So the lane is whoever the store says holds that node's task, and the chart's stored `worker` string is
a human note that must be empty or resolve to a role. This oracle pins both halves, and the two that
must NOT happen: a stage nobody works is still SAID (not blank), and a stale note is still a finding
so the duplication cannot creep back in.

Hermetic where it can be: the fixtures are dicts and a monkeypatched projection. The two live
assertions read the real files, because the point of the fix is the real files.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import flow_layout as fl   # noqa: E402
import role_store as rs    # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    # 1. THE LIVE CHART. These three said "Senior" until 2026-10-25.
    for node in ("preflight", "session_review", "rca"):
        lane = fl.who_works(node)
        check(f"live: {node} names the roles that hold it, not the deleted Senior",
              "Senior" not in lane and "Auditor" in lane, lane)
    # CONSOLE NAMES WHOEVER THE STORE SAYS HOLDS IT (Jas, 2026-10-29, t9567: console moved to Manager only). This
    # pinned "DeepSeek Harness" -- a role literal in a check, the thing t9554 removes -- so a Models-tab change
    # turned it red. It now asks the store, and still fails on the two faults it exists for: 'any role' and nobody.
    import task_routing as _tr
    _d = _tr.load()
    _want = ", ".join(((_d.get("roles") or {}).get(k) or {}).get("name", k) for k in _tr.owners(_d, "console"))
    check("live: console names the role(s) the store gives it, not 'any role'",
          bool(_want) and fl.who_works("console") == _want, (fl.who_works("console"), _want))

    # 2. A STAGE NOBODY WORKS IS SAID, not left blank.
    check("an unheld stage says so out loud",
          fl.who_works("no-such-node-at-all") == "no role holds this stage",
          fl.who_works("no-such-node-at-all"))

    # 3. THE LIVE CHART CARRIES NO STALE ROLE COPY, and the store's own check says so.
    probs = rs.check()
    check("role_store.check() is clean on the live chart", probs == [], probs)

    # 4. THE BOX ITSELF SHOWS THE DERIVED NAME. A stored "Senior" must not reach the label.
    real_workers = fl.workers
    fl.workers = lambda *a, **k: {"x": ["manager"]}
    try:
        doc = {"tasks": [{"id": "x", "name": "Some Stage", "worker": "Senior",
                          "worker_kind": "role", "status": "active"}], "sources": []}
        node = fl.nodes_of(doc)["x"]
        check("a node whose stored note says Senior labels Manager instead",
              any("Manager" in line for line in node["lines"]) and
              not any("Senior" in line for line in node["lines"]), node["lines"])
    finally:
        fl.workers = real_workers

    # 5. A STALE NOTE IS STILL A FINDING -- that is the guard against the duplication coming back.
    store = {"roles": {"manager": {"name": "Manager", "kind": "model", "tasks": [{"task": "x"}]}}}
    nodes = [{"id": "x", "worker": "Senior", "worker_kind": "role"}]
    probs = rs.flow_fk(store, nodes)
    check("a role node whose note names a deleted role is still one finding",
          any("is no role key or name" in x and "Senior" in x for x in probs), probs)
    nodes = [{"id": "x", "worker": "", "worker_kind": "role"}]
    check("an empty note with a holder is not a finding", rs.flow_fk(store, nodes) == [],
          rs.flow_fk(store, nodes))
    check("  and the holder is counted as naming the role",
          rs.flow_fk({"roles": {"m": {"name": "M", "kind": "model", "tasks": [{"task": "x"}]}}},
                     [{"id": "x", "worker": "", "worker_kind": "role"}]) == [])

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
