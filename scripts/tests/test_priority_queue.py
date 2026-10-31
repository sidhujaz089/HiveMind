#!/usr/bin/env python3
"""The priority layer: queued interruptions, production rerouted, interrupted work resumed.

Jas, 2026-09-14: "I want anything to be able to interrupt the manager ... the interruptions need
to be queued. It needs a priority queue and production queue. Production items automatically get
rerouted to another model, unless it was in the middle of something and will resume."
The live case: the supervisor timed out four times because its model held a production lane.
"""
import os
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import dispatch_loop as DL  # noqa: E402
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED, FAILED = PASSED + ok, FAILED + (not ok)
    print(f"  [{'ok' if ok else 'BAD'}]   {label}: {got}")


Q16, G, Q4 = "q16", "gemma", "q4"
M = [G, Q16, Q4]
sup = {"kind": "supervise", "model": Q16, "level": 1, "created": "T1", "key": "fail-1"}
pre = {"kind": "preflight", "model": Q16, "level": 2, "created": "", "key": "pA"}
rev = {"kind": "script-review", "model": G, "level": 3, "created": "T2", "key": "scripts/x.py"}
prod_q16 = {"model": Q16, "kind": "production", "pid": 11, "elapsed_s": 60}

s, pre_pids, q, res = DL.schedule(M, [sup, pre], [])
report("an idle manager starts its FIRST priority item", [i["key"] for i in s], ["fail-1"])
report("the rest are QUEUED, not dropped", [(i["key"], w.startswith("queued behind")) for i, w in q], [("pA", True)])
report("the manager is reserved from production", res, {Q16})

s, pre_pids, q, res = DL.schedule(M, [sup, pre], [prod_q16], can_checkpoint=True)
report("a manager in production is interrupted when the job can resume", pre_pids, [11])
report("nothing starts on it until the lane is free", s, [])

s, pre_pids, q, res = DL.schedule(M, [sup], [{**prod_q16, "elapsed_s": 7200}], can_checkpoint=False)
report("a job hours in that cannot checkpoint is NOT thrown away", pre_pids, [])
report("and the queue says why", "cannot checkpoint" in q[0][1], True)
s, pre_pids, q, res = DL.schedule(M, [sup], [{**prod_q16, "elapsed_s": 300}], can_checkpoint=False)
report("a young job is cheap to interrupt even without a checkpoint", pre_pids, [11])

s, pre_pids, q, res = DL.schedule(M, [sup], [{"model": Q16, "kind": "preflight", "pid": 12}], can_checkpoint=True)
report("priority work is never interrupted by priority work", pre_pids, [])
report("it queues instead", q[0][1].startswith("queued:"), True)

s, pre_pids, q, res = DL.schedule(M, [rev], [{"model": G, "kind": "production", "pid": 13, "elapsed_s": 30}], can_checkpoint=True)
report("ANY model with priority work can be interrupted (auditor too)", pre_pids, [13])

s, pre_pids, q, res = DL.schedule(M, [sup, rev], [])
report("each role's model starts its own first item", sorted(i["key"] for i in s), ["fail-1", "scripts/x.py"])
report("both are reserved from production", res, {Q16, G})

import tempfile
tmp = Path(tempfile.mkdtemp())
(tmp / "workspace" / "briefs").mkdir(parents=True)
(tmp / "workspace" / "briefs" / "a.txt").write_text("TASK: t001\n\nbrief a\n")  # every chunk has a task
real = (DL.REPO, DL.CHECKPOINTS)
DL.REPO, DL.CHECKPOINTS = tmp, tmp / "cp"
try:
    sha = DL.brief_sha("a.txt")
    C = [{"chunk": "pA", "brief": "a.txt", "files": "x.py", "target": "tA"}]
    ok = [{"brief_sha256": sha, "stopped": False}]
    st, sk, _ = DL.plan([G, Q4], C, [], lambda t: True, history=[], objections=ok, reviewer=None)
    report("with no checkpoint, production goes to any free model (rerouted)", [x["model"] for x in st], [G])
    DL.CHECKPOINTS.mkdir()
    label = "brief-" + __import__("hashlib").sha256(b"TASK: t001\n\nbrief a\n").hexdigest()[:8]
    (DL.CHECKPOINTS / f"{label}--q16.json").write_text("{}")
    st, sk, _ = DL.plan([G, Q4], C, [], lambda t: True, history=[], objections=ok, reviewer=None)
    report("an interrupted job with a checkpoint waits for ITS model", st, [])
    report("and says it will resume there", "resume on q16" in dict(sk).get("pA", ""), True)
    st, sk, _ = DL.plan([G, Q16], C, [], lambda t: True, history=[], objections=ok, reviewer=None)
    report("it resumes on its own model when that model is free", [x["model"] for x in st], [Q16])
finally:
    DL.REPO, DL.CHECKPOINTS = real
y = {"event": "finished", "kind": "production", "chunk": "pA", "brief_sha256": "s", "model": Q16, "exit": -15}
report("an interrupted run is not a failure", DL.failures("pA", "s", [y]), [])
# ---- memory: measured footprints, any two fit, three do not
B, GM, Q4R = "qwen3.8:27b-mtp-bf16", "gemma4:26b", "qwen3.8:27b-mtp-q4_K_M"
# GM as a 29.2 GB GX10 model, as these cases were written, whatever REGISTRY says today
# (2026-10-15 it moved to the ThinkPad eGPU at 18.9 GB, where fits() budgets it separately).
# THE MODEL NAMES IN THIS CASE ARE NO LONGER IN THE REGISTRY (2026-10-30). B, GM and Q4R were deleted from it, so
# FOOTPRINT_GIB has no entry for any of them and fits() summed UNKNOWN_FOOTPRINT_GIB three times -- three "unknown"
# models fit, the queue came back empty, and this suite died with IndexError. The case's arithmetic is now its own:
# every footprint and the budget it is compared against are set here, so it tests the RULE and not today's registry.
DL.DEVICE_OF = {k: v for k, v in DL.DEVICE_OF.items() if k != GM}
DL.FOOTPRINT_GIB = {**DL.FOOTPRINT_GIB, GM: 35.0, B: 55.0, Q4R: 25.0}
DL.MEMORY_BUDGET_GIB = 108.0
report("bf16 fits beside gemma", DL.fits(GM, {B}), True)
report("bf16 fits beside q4", DL.fits(Q4R, {B}), True)
report("a THIRD model beside bf16 and gemma does not fit", DL.fits(Q4R, {B, GM}), False)
s, pre_pids, q, res = DL.schedule([GM, B, Q4R], [{**sup, "model": Q4R}], [{"model": B, "kind": "production", "pid": 1}, {"model": GM, "kind": "production", "pid": 2}])
report("priority work waits for memory instead of evicting two running jobs", (s, "waiting for memory" in q[0][1]), ([], True))
# ---- wedged: silent AND model not loaded; either alone is not enough.
# THE PID MUST BE A LIVING ONE (2026-10-29): wedged() now also frees a lane whose process is gone, so a fake
# pid 9 -- which no process owns -- would make these cases pass for the wrong reason. os.getpid() is a process
# that provably exists, which is what "the job is still running, just quiet" means.
lane = {"pid": os.getpid(), "model": B, "log_age_s": 5000}
report("silent and model not loaded -> wedged", [r["pid"] for r in DL.wedged([lane], {GM})], [os.getpid()])
report("silent but model loaded -> just a long attempt", DL.wedged([lane], {B}), [])
report("model not loaded but log recent -> not wedged", DL.wedged([{**lane, "log_age_s": 60}], {GM}), [])

# ---- console messages: production work, first in line, never interrupting
# The console's own model is the MANAGER ROLE (dispatch_loop.CHAT_MODEL, from SERVING.json), not
# a literal: when the manager moved from the bf16 to Q4 on 2026-09-23 this block still named the
# bf16 and went red. The behaviour under test is "prefer the console's model", whichever it is.
CM = DL.CHAT_MODEL
# MEMORY IS NOT UNDER TEST HERE (2026-10-06). The footprints come from REGISTRY.json now, and the
# console model is Swift (95 GB), which really does not fit beside Gemma -- so assign_console rightly
# refused the second model and this block went red for a reason it is not about. Hold memory still.
_real_fp = DL.FOOTPRINT_GIB
DL.FOOTPRINT_GIB = {**_real_fp, CM: 20.0, GM: 20.0}
OWN = [CM, GM]           # both models' roles have Console on their list, CM's first
msgs = [{"id": "m1", "tid": "t"}, {"id": "m2", "tid": "t"}]
st, left = DL.assign_console(msgs, [GM, CM], [], [], owners=OWN)
report("a console message prefers the first Console role's model", st[0]["model"], CM)
report("a second message takes the other free model", [x["model"] for x in st], [CM, GM])
report("models taken by console work are not handed to chunks", left, [])
st, left = DL.assign_console(msgs[:1], [GM, CM], [{"model": CM, "kind": "production", "pid": 5}], [], owners=OWN)
report("a busy console model is NOT interrupted; the message goes to a free model",
       (st[0]["model"], left), (GM, [CM]))
st, left = DL.assign_console(msgs[:1], [GM, CM], [{"model": CM, "kind": "production", "pid": 5},
                                                 {"model": GM, "kind": "production", "pid": 6}], [], owners=OWN)
report("with every model busy the message waits -- nothing is stopped", (st, left), ([], [GM, CM]))
DL.FOOTPRINT_GIB = _real_fp
# THE MODELS TAB DECIDES (2026-10-02): only Console roles' models answer; none -> it waits.
st, left = DL.assign_console(msgs[:1], [GM, CM], [], [], owners=[CM])
report("a model whose role lacks Console is not used", [x["model"] for x in st], [CM])
st, left = DL.assign_console(msgs[:1], [GM, CM], [], [], owners=[])
report("no role has Console: nothing starts", (st, left), ([], [GM, CM]))
d = {"roles": {"senior": {"kind": "model", "model": "s-tag", "tasks": [{"task": "console"}]},
               "manager": {"kind": "model", "model": "m-tag", "tasks": [{"task": "scoping"}]}}}
report("console_models reads the store's Console owners", DL.console_models(d), ["s-tag"])

# 2026-10-01: ONE LANE PER SLOT for priority work. Two slots, one review running -> the next starts.
# PINNED STORE (2026-10-08): these read DL.ROUTING, the LIVE role store -- once Jas set RCA's Max lanes
# to 1 on the Models tab, "starts two" went red for a setting, not a bug. No caps here.
_live_routing = DL.ROUTING
DL.ROUTING = {"roles": {"manager": {"kind": "model", "model": Q16,
                                    "tasks": [{"task": "rca"}, {"task": "session_review"}]}}}
rv = {"model": Q16, "kind": "session-review", "pid": 21}
a, b = {"model": Q16, "kind": "rca", "key": "a", "role": "manager"}, {"model": Q16, "kind": "rca", "key": "b", "role": "manager"}
s2, _p, q2, _r = DL.schedule(M, [a, b], [rv], slots=lambda m: 2)
report("a free slot takes the next priority item", [i["key"] for i in s2], ["a"])
s1, _p, q1, _r = DL.schedule(M, [a, b], [rv], slots=lambda m: 1)
report("with one slot it still queues", [i["key"] for i in s1], [])
s3, _p, _q, _r = DL.schedule(M, [a, b], [], slots=lambda m: 2)
report("an idle two-slot model starts two", [i["key"] for i in s3], ["a", "b"])
DL.ROUTING = _live_routing


print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
