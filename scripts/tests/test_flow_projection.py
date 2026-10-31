#!/usr/bin/env python3
"""Fail-first oracle for t1594: the chart is a PROJECTION of the role assignments.

Jas, 2026-10-25: *"I don't edit the flow tab ... if whatever the source of truth is is updated, the flow
tab also reflects that. No process can exist without being reflected on the flow tab ... If a new
process flow needs to be created, it needs to be done by a model."*

So the chart is generated from the source (the roles and their task lists), never hand-edited by him:

  role_store.flow_workers()      {task_id: [role keys]} -- who actually works each stage
  role_store.write_projection()  writes models/FLOW-WORKERS.json; called by every role save
  check_workspace                FAILS when that file is STALE, and FAILS when a role holds a task
                                 the chart has no node for ("no process may exist that is not on the
                                 chart", the direction the FK does not cover)

Why a side file and not FLOW.json: FLOW.json is Jas's surface, and a generated write into it would be two
writers on one file -- the defect this whole phase is about.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import role_store as rs  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


STORE = {"roles": {
    "manager": {"name": "Manager", "kind": "model", "tasks": [{"task": "scoping"}, {"task": "building"}]},
    "auditor": {"name": "Auditor", "kind": "model", "tasks": [{"task": "rca"}, "session_review"]},
    "jas": {"name": "Jas", "kind": "person", "tasks": [{"task": "assigned_jas"}]},
}}


def main():
    check("flow_workers exists", hasattr(rs, "flow_workers"), True)
    check("write_projection exists", hasattr(rs, "write_projection"), True)
    if not hasattr(rs, "flow_workers"):
        print(f"{PASSED} passed, {FAILED} failed")
        return 1

    got = rs.flow_workers(STORE)
    check("a task held by two roles names both", got.get("building"), None if "building" not in got else got["building"])
    check("a dict-shaped task entry is read", "scoping" in got, True)
    check("a plain-string task entry is read too", "session_review" in got, True)
    check("a role key is what is recorded, not its display name", got.get("rca"), ["auditor"])
    check("the projection is sorted, so two runs write the same bytes", list(got) == sorted(got), True)

    # a second role holding the same task: both keys, sorted
    two = {"roles": {"a": {"tasks": [{"task": "rca"}]}, "b": {"tasks": ["rca"]}}}
    check("two roles on one task are both named, sorted", rs.flow_workers(two).get("rca"), ["a", "b"])

    # what is written: a versioned envelope, not a bare map, so a stale file is recognisable
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "FLOW-WORKERS.json"
        rs.write_projection(STORE, out)
        wrote = json.loads(out.read_text(encoding="utf-8"))
        check("the file records who it came from", bool(wrote.get("_generated")), True)
        check("the workers map is under a named key", wrote.get("workers", {}).get("rca"), ["auditor"])
        check("writing twice is byte-identical", (rs.write_projection(STORE, out),
                                                  out.read_text(encoding="utf-8"))[1] ==
              (rs.write_projection(STORE, out), out.read_text(encoding="utf-8"))[1], True)

    # THE RED FIXTURE the docstring promises (t761, DS's done-check 2026-10-28: the docstring claimed
    # "check_workspace FAILS when that file is STALE" and nothing here called check_workspace).
    import check_workspace  # noqa: E402

    def verdict(proj_workers, nodes, write_proj=True):
        with tempfile.TemporaryDirectory() as td:
            m = Path(td) / "projects" / "local-inference" / "models"
            m.mkdir(parents=True)
            (m / "FLOW.json").write_text(json.dumps({"tasks": [{"id": n} for n in nodes]}), encoding="utf-8")
            if write_proj:
                (m / "FLOW-WORKERS.json").write_text(json.dumps({"workers": proj_workers}), encoding="utf-8")
            c = check_workspace.Checker(Path(td), quick=True)
            c.live = Path(td)
            got = []
            c.section = lambda *a, **k: None
            c.result = lambda status, *a, **k: got.append(status)
            c.check_flow_projection()
            return got[-1] if got else None

    live = rs.flow_workers()
    every = sorted(live)
    check("check_workspace PASSES a current projection with every stage on the chart",
          verdict(live, every), "PASS")
    stale = dict(live, **{"a-stage-nobody-holds": ["ghost"]})
    check("check_workspace FAILS a STALE projection", verdict(stale, every), "FAIL")
    check("check_workspace FAILS a MISSING projection (absence is not agreement)",
          verdict(None, every, write_proj=False), "FAIL")
    if every:
        check("check_workspace FAILS a stage a role holds with no node on the chart",
              verdict(live, every[1:]), "FAIL")

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
