#!/usr/bin/env python3
"""The senior station takes every preflight on its own server (Jas, 2026-09-28: "dedicate this to
preflight ... the Senior builder ... a place holder until we decide the actual Sr model"). Fakes
only: no Ollama, no real ledger, no launched process."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import dispatch_loop as dl  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    # Scoping's claim on the station is live state (manager queue, role lists); every case here
    # pins it off unless it is the case under test (sandbox-red on 2026-10-01).
    dl.scoping_on_station = lambda h, mq=None: {"ours": False, "running": None, "pending": False, "streak": 0}
    # AND THE SENIOR'S LIST IS PINNED for every case: Jas edits it on the Models tab (twice on
    # 2026-10-01), and a test that reads it fails on configuration, not on code.
    import copy as _c0
    dl.ROUTING = _c0.deepcopy(dl.ROUTING)
    dl.ROUTING["roles"]["senior"] = _c0.deepcopy(dl.ROUTING["roles"]["manager"])
    dl.ROUTING["roles"]["senior"]["tasks"] = [{"task": t} for t in ("preflight", "session_review", "rca")]
    tmp = Path(tempfile.mkdtemp(prefix="senior-test-"))
    serving = tmp / "SERVING.json"
    serving.write_text(json.dumps({"roles": {"manager": {"tag": "q"}}}))
    report("no senior role configured -> None (preflights stay on the main server)",
           dl.senior_station(serving), None)
    serving.write_text(json.dumps({"roles": {"senior": {"tag": "q", "endpoint": "http://127.0.0.1:9",
                                                        "lease": "x.json"}}}))
    report("a senior endpoint that does not answer -> None", dl.senior_station(serving), None)

    senior = {"tag": "senior-model", "endpoint": "http://127.0.0.1:11435", "port": 11435,
              "lease": "/tmp/lease-senior.json"}
    chunks = [{"chunk": "p1-a", "brief": "b1", "files": "f.py", "target": "t.py", "brief_sha256": "s1"},
              {"chunk": "p2-b", "brief": "b2", "files": "g.py", "target": "u.py", "brief_sha256": "s2"}]
    launched = []
    real = (dl.launch, dl.classify, dl.ledger_running)
    real_sos, real_y2 = dl.scoping_on_station, dl._yield2
    y2 = []
    dl._yield2 = lambda on: y2.append(on)
    dl.scoping_on_station = lambda h, mq=None: {"ours": False, "running": None, "pending": False, "streak": 0}
    try:
        dl.launch = lambda job: launched.append(job) or (4242, dl.REPO / "x.log")
        dl.classify = lambda ch, running, red_of, history=None, objections=None: (list(ch), [], [])
        dl.ledger_running = lambda history=None: []
        job = dl.senior_slot(senior, chunks, lambda t: True, [])
        report("idle station: the first waiting preflight starts there",
               (job["chunk"], job["kind"], job["model"]), ("p1-a", "preflight", "senior-model"))
        report("  on the senior's server, lease and port",
               (job["endpoint"], job["lease"], job["server_port"]),
               ("http://127.0.0.1:11435", "/tmp/lease-senior.json", 11435))
        dl.ledger_running = lambda history=None: [{"kind": "preflight", "key": "p1-a", "model": "senior-model",
                                                   "pid": 1, "server_port": 11435}]
        launched.clear()
        report("busy station: nothing else starts (one model, one slot)",
               (dl.senior_slot(senior, chunks, lambda t: True, []), launched), (None, []))
        dl.ledger_running = lambda history=None: [{"kind": "session-review", "key": "s", "model": "q",
                                                   "pid": 2, "server_port": 11434}]
        job = dl.senior_slot(senior, chunks, lambda t: True, [])
        report("a job on the MAIN server does not make the senior busy", job["chunk"], "p1-a")
        refused_hist = [{"event": "finished", "kind": "preflight", "chunk": "p1-a", "brief_sha256": "s1",
                         "accepted": False, "exit": 2}]
        defects = []
        real_def = dl._preflight_defect
        dl._preflight_defect = lambda c: defects.append(c["chunk"])
        job = dl.senior_slot(senior, chunks, lambda t: True, refused_hist)
        dl._preflight_defect = real_def
        report("a brief its preflight already refused is skipped, not relaunched (head-of-line)",
               (job["chunk"], defects), ("p2-b", ["p1-a"]))
        edited = [dict(chunks[0], brief_sha256="s1-edited"), chunks[1]]
        dl._preflight_defect = lambda c: None
        real_obj = dl.OBJECTIONS
        dl.OBJECTIONS = tmp / "objections.jsonl"
        dl.OBJECTIONS.write_text(json.dumps({"brief_sha256": "s1", "stopped": True}) + "\n")
        dl._preflight_defect = lambda c: None
        report("a MODEL objection whose verdict expired is re-reviewed, not held (2026-09-29)",
               dl.senior_slot(senior, chunks, lambda t: True, refused_hist)["chunk"], "p1-a")
        dl.OBJECTIONS.write_text(json.dumps({"brief_sha256": "s1", "stopped": True, "verdict": "NOT SATISFIABLE",
                                             "note": "checked without asking the model; missing: ['x']"}) + "\n")
        report("a MECHANICAL refusal's row is not a verdict: still held (p154, 62 relaunches, 2026-09-30)",
               dl.senior_slot(senior, chunks, lambda t: True, refused_hist)["chunk"], "p2-b")
        dl.OBJECTIONS = real_obj
        report("  and runs again once the brief text changes",
               dl.senior_slot(senior, edited, lambda t: True, refused_hist)["chunk"], "p1-a")
        dl._preflight_defect = real_def
        launched.clear()
        job = dl.senior_slot(senior, chunks, lambda t: True, [], dry_run=True)
        report("dry run plans and launches nothing", (job["chunk"], launched), ("p1-a", []))
        # IDLE, IT HELPS THE MANAGER (Jas, 2026-09-29): no preflight waiting -> a session review or RCA.
        real_pi = dl.priority_items
        dl.priority_items = lambda *a, **k: [
            {"kind": "script-review", "key": "x.py", "chunk": "x.py", "path": "x.py"},
            {"kind": "session-review", "key": "s1.json", "chunk": "s1.json", "path": "/r/s1.json"},
            {"kind": "rca", "key": "f1.json", "chunk": "f1.json", "path": "/r/f1.json"}]
        dl.ledger_running = lambda history=None: []
        launched.clear()
        # THE LIST UNDER TEST IS PINNED, not read from ROLES.json (2026-10-01: Jas reordered the
        # Senior's list and dropped session review, and this case failed on configuration).
        import copy as _copy
        real_routing = dl.ROUTING
        dl.ROUTING = _copy.deepcopy(real_routing)
        dl.ROUTING["roles"]["senior"]["tasks"] = [{"task": t} for t in ("preflight", "session_review", "rca")]
        job = dl.senior_slot(senior, [], lambda t: True, [])
        report("idle with no preflight: the senior takes the next session review, on its own server",
               (job["kind"], job["key"], job["server_port"], job["model"]),
               ("session-review", "s1.json", 11435, "senior-model"))
        dl.ROUTING = real_routing
        report("  and never a script review (not the manager's work)", dl.SENIOR_TODO,
               [("session-review", "s1.json"), ("rca", "f1.json")])
        job = dl.senior_slot(senior, chunks, lambda t: True, [])
        report("a waiting preflight still goes first", (job["kind"], job["chunk"]), ("preflight", "p1-a"))
        dl.priority_items = real_pi
    finally:
        dl.launch, dl.classify, dl.ledger_running = real

    real_classify = dl.classify
    try:
        dl.classify = lambda ch, running, red_of, history=None, objections=None: (list(ch), [], [])
        with_pf = dl.priority_items(chunks, [], lambda t: True, history=[], objections=[])
        without = dl.priority_items(chunks, [], lambda t: True, history=[], objections=[], preflight=False)
        report("with a senior station the main queue carries no preflights",
               (sum(i["kind"] == "preflight" for i in with_pf), sum(i["kind"] == "preflight" for i in without)),
               (2, 0))
    finally:
        dl.classify = real_classify
    # 2026-10-01: SCOPING ON THE SENIOR'S STATION, shared one job at a time, with the Series turn.
    try:
        dl.launch = lambda job: launched.append(job) or (4242, dl.REPO / "x.log")
        dl.classify = lambda ch, running, red_of, history=None, objections=None: (list(ch), [], [])
        dl.ledger_running = lambda history=None: []
        dl.scoping_on_station = lambda h, mq=None: {"ours": True, "running": "t1", "pending": True, "streak": 0}
        report("a live scoping run holds the station: the Senior starts nothing",
               dl.senior_slot(senior, chunks, lambda t: True, []), None)
        dl.scoping_on_station = lambda h, mq=None: {"ours": True, "running": None, "pending": True, "streak": 1}
        real_entry = dl.task_routing.entry
        dl.task_routing.entry = lambda d, role, kind: {"task": "scoping", "series": 2}
        report("  between scoping tasks, below the Series limit: still scoping's",
               dl.senior_slot(senior, chunks, lambda t: True, []), None)
        dl.scoping_on_station = lambda h, mq=None: {"ours": True, "running": None, "pending": True, "streak": 2}
        job = dl.senior_slot(senior, chunks, lambda t: True, [])
        report("  at the Series limit the waiting preflight gets one turn, and YIELD2 is raised",
               (job and job["kind"], y2[-1]), ("preflight", True))
        import modellease as _ml
        real_slots = _ml.slots
        _ml.slots = lambda path, model=None: 2
        dl.scoping_on_station = lambda h, mq=None: {"ours": True, "running": "t1", "pending": True, "streak": 0}
        job = dl.senior_slot(senior, chunks, lambda t: True, [])
        report("two slots, scoping in one: the other takes the waiting preflight", job and job["kind"], "preflight")
        dl.scoping_on_station = lambda h, mq=None: {"ours": True, "running": None, "pending": True, "streak": 0}
        job = dl.senior_slot(senior, chunks, lambda t: True, [])
        report("two slots, none used: a preflight may start; scoping still has the other",
               job and job["kind"], "preflight")
        _ml.slots = real_slots
        dl.task_routing.entry = real_entry
    finally:
        dl.launch, dl.classify, dl.ledger_running = real
        dl.scoping_on_station, dl._yield2 = real_sos, real_y2
    # SCOPING IS RANKED, NOT SWITCHED (Jas, 2026-10-02: the Senior did RCAs, 9th on its list, while
    # Scoping, 5th, starved -- it only counted when it sat above Preflight).
    import copy as _c2, manager_next as _mn, who_works as _ww
    real = (dl.launch, dl.classify, dl.ledger_running, dl.priority_items, dl.ROUTING, dl._scope2)
    real_sos2 = dl.scoping_on_station
    real_mn, real_ww = _mn.upcoming, _ww.manager_queue_now
    flags = []
    try:
        dl.launch = lambda job: launched.append(job) or (4242, dl.REPO / "x.log")
        dl.classify = lambda ch, running, red_of, history=None, objections=None: ([], [], [])
        dl.ledger_running = lambda history=None: []
        dl.priority_items = lambda *a, **k: [{"kind": "rca", "key": "f1.json", "chunk": "f1.json", "path": "/r/f1.json"}]
        dl._scope2 = lambda on: flags.append(on)
        dl.ROUTING = _c2.deepcopy(real[4])
        dl.ROUTING["roles"]["senior"]["tasks"] = [{"task": t} for t in ("preflight", "scoping", "rca")]
        dl.scoping_on_station = lambda h, mq=None: {"ours": False, "running": None, "pending": True, "streak": 0, "rankable": True}
        report("Scoping above RCA, the manager queue has a task: the Senior leaves the slot for scoping",
               (dl.senior_slot(senior, [], lambda t: True, []), flags[-1]), (None, True))
        dl.ROUTING["roles"]["senior"]["tasks"] = [{"task": t} for t in ("preflight", "rca", "scoping")]
        job = dl.senior_slot(senior, [], lambda t: True, [])
        report("  RCA above Scoping: the RCA starts, and the flag is cleared", (job and job["kind"], flags[-1]), ("rca", False))
        dl.ROUTING["roles"]["senior"]["tasks"] = [{"task": t} for t in ("preflight", "scoping", "rca")]
        dl.scoping_on_station = lambda h, mq=None: {"ours": False, "running": None, "pending": False, "streak": 0, "rankable": False}
        job = dl.senior_slot(senior, [], lambda t: True, [])
        report("  nothing for scoping: the next task down (RCA) runs", job and job["kind"], "rca")
        dl.scoping_on_station = lambda h, mq=None: {"ours": False, "running": None, "pending": True, "streak": 0, "rankable": False}
        job = dl.senior_slot(senior, [], lambda t: True, [])
        report("  the queue is mid-task on the main server: the Senior does not sit waiting for it",
               job and job["kind"], "rca")
    finally:
        dl.launch, dl.classify, dl.ledger_running, dl.priority_items, dl.ROUTING, dl._scope2 = real
        _mn.upcoming, _ww.manager_queue_now = real_mn, real_ww
        dl.scoping_on_station = real_sos2
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
