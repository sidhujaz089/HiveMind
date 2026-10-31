#!/usr/bin/env python3
"""t1345 regression: a WAITING build takes at most one lane, so it cannot hold back
priority work.

8cb23029 made schedule() zero out the free lanes the moment a production item sat in the
model's waiting list, so Swift (4 slots, 1 job running) held every RCA as
"queued: swift is doing priority work" with lanes still free. ab51d909 (2026-10-16) stopped
that: dispatch_loop.py:1463-1468 keeps free = slots - running and lets at most ONE waiting
build ride along (mine = non-prod + [prod][:1]), so the priority work takes the rest.

This is a REGRESSION GUARD for a committed fix, not a fail-first oracle: it is GREEN on main
because ab51d909 is present, and RED if the free=0 zeroing line (8cb23029) is re-introduced.

Hermetic (scope_lint oracle-live-state): it never reads the Models-tab routing store. It builds
its OWN routing fixture, materialises it in a temp dir, and points the module at a deepcopy of
it for the duration of the run, restoring the original afterwards.
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # scripts/tests/ -> repo root
sys.path.insert(0, str(ROOT / "scripts"))
import dispatch_loop as DL                         # noqa: E402

SWIFT = "swift"

# A fixture of OUR OWN -- not read from the live store. One role on Swift whose task list puts
# the RCA first (top priority) so the regression is unambiguous; "building" and "script-review"
# are present so every kind the test references resolves (task_routing.owner/position).
FIXTURE = {
    "roles": [
        {
            "name": "manager",
            "kind": "model",
            "model": SWIFT,
            "tasks": [
                {"task": "rca"},
                {"task": "building"},
                {"task": "script-review"},
            ],
        }
    ]
}


def run():
    # oracle-live-state: keep a self-contained fixture in a temp dir and point the module at a
    # copy, so a Models-tab edit or a new ledger row cannot flip this test.
    tdir = tempfile.mkdtemp(prefix="t1345-")
    with open(os.path.join(tdir, "routing.json"), "w", encoding="utf-8") as fh:
        json.dump(FIXTURE, fh)
    saved = copy.deepcopy(DL.ROUTING)
    DL.ROUTING = copy.deepcopy(FIXTURE)
    try:
        rca   = {"model": SWIFT, "kind": "rca", "key": "rca-1"}
        build = {"model": SWIFT, "kind": "production", "key": "bld-1"}
        # one non-production job already running -> 3 of 4 slots free. This lands in the
        # free-lanes branch (dispatch_loop.py:1461), where the old code zeroed free.
        running = [{"model": SWIFT, "kind": "script-review", "key": "run-1", "pid": 7}]

        starts, _preempt, queued, _reserved = DL.schedule(
            [SWIFT], [rca, build], running, slots=lambda m: 4)
        started = [i["key"] for i in starts]

        # THE REGRESSION: with 3 free lanes the priority item (RCA) must start -- it must not be
        # held as "is doing priority work". (The build legitimately takes its one reserved lane
        # and may ITSELF be queued with that reason at dispatch_loop.py:1483, so we assert the
        # RCA specifically, not that NOTHING is queued.)
        assert "rca-1" in started, (
            "waiting build zeroed the free lanes: RCA did not start. "
            f"started={started} queued={queued}")
        assert not any(i.get("key") == "rca-1" and "is doing priority work" in why
                       for (i, why) in queued), (
            "RCA was held as 'is doing priority work' with 3 lanes free: "
            f"queued={queued}")

        # FIVE waiting builds still cannot crowd the priority item out: a build holds at most one
        # lane (dispatch_loop.py:1466-1468), so the RCA still starts beside them.
        builds = [{"model": SWIFT, "kind": "production", "key": f"b{i}"} for i in range(5)]
        starts2, _p, queued2, _r = DL.schedule(
            [SWIFT], [rca] + builds, running, slots=lambda m: 4)
        assert "rca-1" in [i["key"] for i in starts2], (
            "5 waiting builds crowded the RCA out: "
            f"started={[i['key'] for i in starts2]} queued={queued2}")
        print("ok: a waiting build holds at most one lane; priority work starts")
    finally:
        DL.ROUTING = saved
    return 0


if __name__ == "__main__":
    sys.exit(run())
