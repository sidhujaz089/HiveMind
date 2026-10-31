#!/usr/bin/env python3
"""Who is working on what (Jas, 2026-09-29): each role's NOW and NEXT for the Hub's tabs. Fakes only:
a temporary progress.log and snapshot, no dispatcher, no model."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import who_works as ww  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    with tempfile.TemporaryDirectory() as d:
        log = Path(d) / "progress.log"
        log.write_text("05:10:48Z START t459 (task t459)\n05:35:40Z END t459 rc=0\n"
                       "05:35:40Z START t460 (task t460)\n")
        report("an open START is the manager's task", ww.manager_queue_now(log), "t460")
        log.write_text(log.read_text() + "06:02:35Z YIELD priority work is waiting; not starting t461\n")
        report("a YIELD after START closes it (restart killed t460 with no END)",
               ww.manager_queue_now(log), None)

        # THE QUEUE'S TASK GOES ON THE TILE OF THE SERVER IT RUNS ON (2026-10-02).
        log.write_text("19:52:51Z START t648 (task t648) station 2\n")
        report("a START on station 2 is the Senior's", ww.manager_queue_station(log), 2)
        log.write_text("19:52:51Z START t648 (task t648) station 1\n")
        report("a START on station 1 is the Manager's", ww.manager_queue_station(log), 1)
        log.write_text("05:35:40Z START t460 (task t460)\n")
        report("an old START with no station is the main server's", ww.manager_queue_station(log), 1)
        log.write_text("19:52:51Z START t648 (task t648) station 2\n19:59:00Z END t648 rc=0\n")
        report("an ENDed task has no station", ww.manager_queue_station(log), None)

        # WORK NO ROLE HAS IS NOBODY'S NEXT (2026-10-02): the tick filtered role != "coder", so the
        # held session reviews showed as the Manager's Next after Jas removed the task.
        src = (Path(__file__).resolve().parents[1] / "dispatch_loop.py").read_text()
        report("the Manager's Next is the Manager's own items",
               ('manager_waiting=[(i["kind"], i["key"]) for i in _items if i.get("role") == "manager"]' in src,
                'if i.get("role") != "coder"],' in src), (True, False))

        snap = Path(d) / "who.json"
        # These checks pin the tiles with Building on the coder's list (the Frontline tile); which
        # role that is comes from the store (2026-10-07), checked separately below.
        _real_build_role, ww.build_role = ww.build_role, (lambda: "frontline")
        running = [{"kind": "session-review", "key": "s1.json", "model": "q4", "pid": 1, "server_port": 11434},
                   {"kind": "manager", "key": "session review: s1 part 1/4", "model": "q4", "pid": 2,
                    "server_port": 11434},
                   {"kind": "production", "brief": "brief-p158-t267-test-failures-ceiling.txt", "model": "g",
                    "pid": 3, "server_port": 11434},
                   {"kind": "production", "brief": "mark the inbox README heading", "model": "faketest",
                    "pid": 5, "server_port": 11434},
                   # CARRIES ITS ROLE (2026-10-18): the senior station's jobs are launched with role
                   # "senior" and that is what puts them on its tile -- not the port (Jas: the scheduler
                   # matches the Roles tab; test_who_works_no_role_fallback pins "no port guesses").
                   {"kind": "preflight", "key": "p160-t274-y", "model": "q4", "pid": 4, "server_port": 11435,
                    "role": "senior"}]
        ww.record(running, senior={"tag": "q4", "port": 11435},
                  senior_waiting=[("preflight", "p161-t278-a"), ("preflight", "p162-t297-b")],
                  manager_waiting=[("session-review", "s2.json")],
                  frontline_waiting=[("production", "p159-t271-c")], path=snap)
        import json
        doc = json.loads(snap.read_text())
        r = doc["roles"]
        report("one manager job reads as one line", [i["key"] for i in r["manager"]["now"]], ["s1.json"])
        report("the senior is what carries its role", [i["key"] for i in r["senior"]["now"]],
               ["p160-t274-y"])
        report("a build is named by its brief, with its task", [(i["key"], i["task"]) for i in r["frontline"]["now"]],
               [("p158-t267-test-failures-ceiling", "t267")])
        report("  and a test suite's fake coder run is not a build", "faketest" in str(r["frontline"]), False)
        # A BUILD SHOWS ON THE ROLE THAT HAS BUILDING (Jas, 2026-10-07: every task moved to the
        # Manager, and a build still showed on Frontline).
        ww.build_role = lambda: "manager"
        snap2 = Path(d) / "who2.json"
        ww.record(running[2:3], senior=None, main_port=11434, path=snap2)
        r2 = json.loads(snap2.read_text())["roles"]
        report("with Building on the Manager's list, a build shows on the Manager",
               ([i["key"] for i in r2["manager"]["now"]], r2["frontline"]["now"]),
               (["p158-t267-test-failures-ceiling"], []))
        ww.build_role = _real_build_role
        doc["roles"] = r
        m = ww.marks(doc)
        report("only the first NEXT is flagged", (m.get("t278"), m.get("t297")), (("next", "SENIOR NEXT"), None))
        report("NOW is flagged WORKING", m.get("t274"), ("now", "SENIOR WORKING"))
    # TESTS IN FLIGHT (Jas, 2026-09-29: "Make the tests appear in the production stages")
    procs = {
        1: {"cmd": "/usr/lib/systemd/systemd --user", "ppid": 0, "start": 0},
        10: {"cmd": "python3 scripts/merge_gate.py coder/brief-aaaaaaaa coder/brief-bbbbbbbb", "ppid": 1, "start": 0},
        11: {"cmd": "/usr/bin/python3 /tmp/mergegate-x/scripts/tests/test_outer.py", "ppid": 10, "start": 0},
        12: {"cmd": "/usr/bin/python3 /tmp/d/coder.py --brief mark it --model faketest", "ppid": 11, "start": 0},
        13: {"cmd": "/usr/bin/python3 /tmp/coder-1/scripts/tests/test_inner.py", "ppid": 12, "start": 0},
        20: {"cmd": "/usr/bin/python3 /tmp/greencount-z/fake.py 4000", "ppid": 1, "start": 0},
    }
    t = ww.test_runs(procs)
    report("one run per OUTERMOST suite", [r["test"] for r in t["runs"]], ["test_outer.py"])
    report("  with what started it", t["runs"][0]["origin"], "merge gate (2 branches)")
    report("  and what it is doing inside", t["runs"][0]["inside"], "test_inner.py")
    report("a helper left running after its test exited is a leak", [o["pid"] for o in t["orphans"]], [20])
    # A RUNNING TEST IS FLAGGED ON ITS TASK'S CARD, with the ROLE (Jas, 2026-09-29)
    import types
    real = sys.modules.get("task_board")
    sys.modules["task_board"] = types.SimpleNamespace(
        brief_tasks=lambda repo: ({"p9-t900-x": "t900"}, {"aaaaaaaa": "t800", "bbbbbbbb": "t801"}))
    procs[30] = {"cmd": "python3 coder.py --brief-file /w/briefs/brief-p9-t900-x.txt --model g", "ppid": 1, "start": 0}
    procs[31] = {"cmd": "/usr/bin/python3 /tmp/coder-9/scripts/tests/test_x.py", "ppid": 30, "start": 0}
    m = ww.test_marks(procs=procs)
    sys.modules.pop("task_board")
    if real is not None:
        sys.modules["task_board"] = real
    report("a merge gate's test flags each branch's task as CLAUDE TESTING",
           (m.get("t800", ("",))[0], m.get("t801", ("",))[0]), ("CLAUDE TESTING", "CLAUDE TESTING"))
    report("a build re-running its oracle flags its task as FRONTLINE TESTING",
           m.get("t900", ("",))[0], "FRONTLINE TESTING")
    # THE RECORDED ROLE WINS OVER THE PORT (2026-10-02): a session review the store gave the
    # Frontline ran on the main server and read as the Manager's.
    import who_works as _W
    report("a job the Frontline took shows as the Frontline's, on any port",
           _W.role_of("session-review", 11434, 11435, 11434, "coder"), "frontline")
    report("a Senior job on the main server shows as the Senior's",
           _W.role_of("rca", 11434, 11435, 11434, "senior"), "senior")
    report("an old row with no role still falls back to the port",
           _W.role_of("rca", 11434, 11435, 11434), "manager")
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
