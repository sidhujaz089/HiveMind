#!/usr/bin/env python3
"""task_routing.round_robin honours Max lanes (Jas, 2026-10-08): a task at its cap is skipped and
its share goes to the rest; 0 = no limit; Building is fixed at 1. Hermetic: a fake role store."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import task_routing as T  # noqa: E402

FAILED = 0


def check(label, got, want):
    global FAILED
    FAILED += got != want
    print(f"  [{'ok' if got == want else 'BAD'}] {label}: {got!r}")


D = {"roles": {"manager": {"kind": "model", "model": "swift", "tasks": [
    {"task": "building", "max_lanes": 5}, {"task": "rca", "max_lanes": 1},
    {"task": "session_review", "max_lanes": 1}, {"task": "script_review", "max_lanes": 0}]}}}


def items(kind, n):
    return [{"kind": kind, "role": "manager", "key": f"{kind}{i}"} for i in range(n)]


def kinds(picked):
    out = {}
    for i in picked:
        out[i["kind"]] = out.get(i["kind"], 0) + 1
    return out


many = items("rca", 20) + items("session-review", 20) + items("script-review", 20)
got, _ = T.round_robin(D, many, {}, 6)
check("RCA and session review capped at 1, script review (0) takes the rest", kinds(got),
      {"rca": 1, "session-review": 1, "script-review": 4})
got, _ = T.round_robin(D, items("rca", 20), {"rca": 1}, 6)
check("RCA already holds its 1 lane: nothing more starts", kinds(got), {})
got, _ = T.round_robin(D, items("rca", 20), {}, 6)
check("only RCA waiting, cap 1: one lane, the others stay free", kinds(got), {"rca": 1})
check("Building's Max lanes is read from the store (editable since 2026-10-08)", T.max_lanes(D, "production"), 5)
check("Building not set = 1, the old one-build rule",
      T.max_lanes({"roles": {"m": {"kind": "model", "tasks": [{"task": "building"}]}}}, "production"), 1)
got, won = T.round_robin({"roles": {"m": {"kind": "model", "model": "swift", "tasks": [
    {"task": "building", "max_lanes": 3}]}}}, [], {}, 4, build_waiting=5)
check("5 builds ready, Building Max lanes 3, 4 free lanes: 3 build lanes won", won, 3)
one = {"roles": {"m": {"kind": "model", "model": "swift", "tasks": [{"task": "building"}]}}}
got, won = T.round_robin(one, [], {"production": 1}, 3, build_waiting=True)
check("Building not set, a build already running: a second does not win a lane", won, 0)
got, won = T.round_robin(D, [], {"production": 1}, 3, build_waiting=4)
check("Building Max lanes 5, one running, 4 ready, 3 free: 3 more win lanes", won, 3)
check("absent max_lanes reads as 0 (no limit)",
      T.max_lanes({"roles": {"m": {"kind": "model", "tasks": [{"task": "rca"}]}}}, "rca"), 0)
# ROLE LANES (Jas, 2026-10-08): a role's Lanes caps it across all its tasks; the model serves the sum.
R = {"roles": {"manager": {"kind": "model", "model": "swift", "lanes": 2, "tasks": [
         {"task": "rca"}, {"task": "session_review"}, {"task": "script_review"}]},
     "senior": {"kind": "model", "model": "swift", "lanes": 3, "tasks": [{"task": "preflight"}]},
     "idle": {"kind": "model", "model": "swift", "lanes": 5, "tasks": []}}}
got, _ = T.round_robin(R, many, {}, 6)
check("role with 2 lanes, 3 tasks waiting, 6 free: only 2 start", len(got), 2)
got, _ = T.round_robin(R, many, {"rca": 2}, 6, running_roles={"manager": 2})
check("role already holding its 2 lanes: nothing more starts", got, [])
check("absent lanes = no role limit (None)", T.role_lanes({"roles": {"m": {"kind": "model", "tasks": [{"task": "rca"}]}}}, "m"), None)
check("a role with no tasks holds no lanes", T.role_lanes(R, "idle"), 0)
check("the model serves the sum of its roles' lanes", T.model_lanes(R, "swift"), 5)
check("no role on a model: None (the engine's own figure stands)", T.model_lanes(R, "other"), None)
# plan() FILLS UP TO BUILDING'S MAX LANES (2026-10-08; was one build per model).
import dispatch_loop as DL  # noqa: E402
ready = [{"chunk": f"p{i}", "brief": f"b{i}", "brief_sha256": str(i)} for i in range(5)]
DL.classify = lambda *a, **k: ([], list(ready), [])
DL.failures = lambda *a, **k: []
DL.checkpoint_model = lambda *a, **k: None
DL.fits = lambda *a, **k: True
for cap, want in ((None, 1), (3, 3), (0, 4)):
    t = {"task": "building"} if cap is None else {"task": "building", "max_lanes": cap}
    DL.ROUTING = {"roles": {"m": {"kind": "model", "model": "swift", "tasks": [t]}}}
    starts, _, _ = DL.plan(["swift"], [], [], None, slots=lambda m: 4)
    check(f"plan(): Building Max lanes {cap}, 4 free slots, 5 ready: {want} build(s) start", len(starts), want)
# A HARNESS FIX RELEASES WHAT IT PARKED (2026-10-08).
import tempfile, json as _json  # noqa: E401,E402
hf = Path(tempfile.mkdtemp()) / "harness-fixed.json"
DL.HARNESS_FIXED = hf
DL._harness_bug_keys = lambda: set()
hist = [{"event": "finished", "kind": "production", "chunk": "c1", "brief_sha256": "s", "exit": 1,
         "at": "2026-10-08T18:00:00Z", "model": "swift"}]
check("one failure parks the brief", DL.futile("c1", "s", hist)[0], True)
hf.write_text(_json.dumps({"at": "2026-10-08T19:00:00Z", "why": "test"}))
check("a harness fix recorded after it releases the brief", DL.futile("c1", "s", hist)[0], False)
hist.append({**hist[0], "at": "2026-10-08T20:00:00Z"})
check("a failure after the fix parks it again", DL.futile("c1", "s", hist)[0], True)
# A MODEL WITH FREE LANES IS NOT TAKEN BY ITS BUILD (2026-10-08).
DL.ROUTING = {"roles": {"m": {"kind": "model", "model": "swift", "tasks": [
    {"task": "script_review", "interrupt": False}, {"task": "building", "max_lanes": 0}]}}}
sr = [{"kind": "script-review", "role": "m", "model": "swift", "key": f"s{i}", "interrupt": False} for i in range(3)]
DL.fits = lambda *a, **k: True
st, pre, q, res = DL.schedule(["swift"], sr, [{"model": "swift", "kind": "production", "pid": 9}],
                              slots=lambda m: 4)
check("one build running, 4 lanes: waiting script reviews start (no 'wait for the build')", len(st), 3)
st, pre, q, res = DL.schedule(["swift"], sr, [{"model": "swift", "kind": "production", "pid": 9}],
                              slots=lambda m: 1)
check("one lane, a build on it, Interrupt off: they wait for the build, as before", len(st), 0)
# BUILDS LEAVE ROOM FOR SCOPING (2026-10-08: 4 builds + 2 scoping workers on 4 lanes -> reboot).
DL.classify = lambda *a, **k: ([], list(ready), [])
DL.ROUTING = {"roles": {"m": {"kind": "model", "model": "swift", "tasks": [{"task": "building", "max_lanes": 0}]}}}
scoping = [{"kind": "scoping", "model": "swift", "key": "scope:t1", "scoping_worker": True},
           {"kind": "scoping", "model": "swift", "key": "scope:t2", "scoping_standin": True}]
starts, _, _ = DL.plan(["swift"], [], scoping, None, slots=lambda m: 4)
check("4 lanes, 1 scoping worker + 1 lane granted to Scoping: only 2 builds start", len(starts), 2)
import scoping_lanes as SL  # noqa: E402
import lane_status as LS  # noqa: E402
import modellease as ML  # noqa: E402
ML._engine_slots = lambda m: 4
LS.lanes = lambda: [{"script": "coder.py", "argv": []}] * 4
check("backstop: 4 builds live on 4 lanes -> no room for a scoping worker", SL.free_lanes(), 0)
LS.lanes = lambda: [{"script": "coder.py", "argv": []}, {"script": "ask_q16.py", "argv": ["--purpose", "manager: t1"]}]
check("backstop: 1 build live -> 3 lanes for scoping (its own worker not subtracted)", SL.free_lanes(), 3)
sys.exit(1 if FAILED else 0)
