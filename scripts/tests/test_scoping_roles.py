#!/usr/bin/env python3
"""ONE SCOPING LOOP PER ROLE (Jas, 2026-10-14: "I want the auditor and Manager working together ... I just
need to get them both scoping"). Each role's loop counts only its own workers, and a role other than the
Manager gets its own lanes -- never the Manager's grant -- and none when it is stopped or Scoping is off
its list."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import scoping_lanes as SL  # noqa: E402
import lane_status  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def store(lanes, tasks):
    return {"roles": {"manager": {"kind": "model", "model": "swift", "lanes": 6,
                                  "tasks": [{"task": "scoping"}]},
                      "auditor": {"kind": "model", "model": "q4", "lanes": lanes, "tasks": tasks}}}


def main():
    me = os.getpid()
    with tempfile.TemporaryDirectory() as tmp:
        q = Path(tmp)
        (q / "claims").mkdir()
        (q / "claims" / "t1").write_text(f"{me}\n")            # an old claim: no role = the Manager's
        (q / "claims" / "t2").write_text(f"{me} manager\n")
        (q / "claims" / "t3").write_text(f"{me} auditor\n")
        report("every live worker, no role asked", [t for t, _ in SL.live(q)], ["t1", "t2", "t3"])
        report("the Manager's: its own and the old claims without a role",
               [t for t, _ in SL.live(q, role="manager")], ["t1", "t2"])
        report("the Auditor's: only its own", [t for t, _ in SL.live(q, role="auditor")], ["t3"])
        report("a shared claim still refuses the other role's loop", SL.claim("t3", me, q), False)

    real = lane_status.lanes
    try:
        lane_status.lanes = lambda: []
        scoping = [{"task": "scoping", "max_lanes": 0}]
        report("the Auditor gets its own lanes", SL.role_target("auditor", store(2, scoping)), 2)
        report("the Auditor stopped (lanes 0): nothing", SL.role_target("auditor", store(0, scoping)), 0)
        report("Scoping off its list: nothing", SL.role_target("auditor", store(2, [{"task": "rca"}])), 0)
        report("its Scoping Max lanes caps it",
               SL.role_target("auditor", store(3, [{"task": "scoping", "max_lanes": 1}])), 1)
        lane_status.lanes = lambda: [{"script": "ask_q16.py", "argv": [str(ROOT / "scripts" / "ask_q16.py"), "--model", "q4",
                                                                      "--purpose", "rca: x"]}]
        report("another job on its model takes a lane", SL.role_target("auditor", store(2, scoping)), 1)
        lane_status.lanes = lambda: [{"script": "ask_q16.py", "argv": [str(ROOT / "scripts" / "ask_q16.py"), "--model", "swift",
                                                                      "--purpose", "rca: x"]}]
        report("a job on the Manager's model does not", SL.role_target("auditor", store(2, scoping)), 2)
    finally:
        lane_status.lanes = real
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
