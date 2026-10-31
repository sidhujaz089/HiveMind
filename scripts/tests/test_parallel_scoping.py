#!/usr/bin/env python3
"""Parallel scoping (Jas, 2026-10-08): claims never double-take a task, the worker target follows the
scheduler's grant within Scoping's Max lanes (at least 1), manager_next skips claimed tasks, and
Scoping takes its turn in the lane rotation counting its live workers. Hermetic: temp dirs, fake
role stores, no model, no dispatcher tick."""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import scoping_lanes as L  # noqa: E402
import task_routing as T  # noqa: E402

FAILED = 0


def check(label, got, want):
    global FAILED
    FAILED += got != want
    print(f"  [{'ok' if got == want else 'BAD'}] {label}: {got!r}")


q = Path(tempfile.mkdtemp())

# ---- claims
check("first claim of t1 wins", L.claim("t1", os.getpid(), q), True)
check("second claim of t1 loses", L.claim("t1", os.getpid(), q), False)
check("live lists the claim", [t for t, _ in L.live(q)], ["t1"])
dead = subprocess.Popen(["true"]); dead.wait()
(q / "claims" / "t2").write_text(f"{dead.pid}\n")
check("a dead worker's claim is pruned", [t for t, _ in L.live(q)], ["t1"])
check("and its task can be claimed again", L.claim("t2", os.getpid(), q), True)
L.release("t1", q); L.release("t2", q)
check("release frees it", L.live(q), [])

# ---- target: grant, floor 1, Max lanes cap
def store(cap):
    return {"roles": {"manager": {"kind": "model", "model": "swift",
                                  "tasks": [{"task": "scoping", "max_lanes": cap}, {"task": "rca"}]}}}


# HERMETIC (2026-10-08): target() is also capped by free_lanes(), which reads the LIVE box (/proc and
# Swift's lanes); the grant and Max-lanes checks here must not depend on what the machine is running.
# test_max_lanes covers free_lanes itself with stubs.
L.free_lanes = lambda model="swift": None
check("no grant: one worker", L.target(q, d=store(0)), 1)
L.write_grant(4, q)
check("grant 4, Max lanes 0 (no limit): 4", L.target(q, d=store(0)), 4)
check("grant 4, Max lanes 2: 2", L.target(q, d=store(2)), 2)
L.write_grant(0, q)
check("a fresh grant of 0 is obeyed (one lane must not be held by scoping forever)", L.target(q, d=store(0)), 0)
L.write_grant(5, q, now=time.time() - L.GRANT_MAX_AGE_S - 5)
check("a stale grant (dead scheduler) falls back to one", L.target(q, d=store(0)), 1)

# ---- manager_next skips claimed tasks
import manager_next as M  # noqa: E402
qf = q / "QUEUE.md"
qf.write_text("1. t11 — a\n2. t12 — b\n")
tasks = q / "tasks"; tasks.mkdir()
for t in ("t11", "t12"):
    (tasks / f"{t}--x.md").write_text(f"---\nid: {t}\nstatus: queued\n---\n")
M.in_production = lambda loop=None, briefs=None: set()
M._big_project = lambda tid, tasks=None: False
M.last_worked = lambda tid, sessions=None: 0
picked, skipped = M.upcoming(2, queue=qf, tasks=tasks, claims=["t11"])
check("a claimed task is not offered", picked, ["t12"])
check("and the skip says why", dict(skipped).get("t11"), "being scoped by another worker")

# ---- Scoping in the lane rotation
import dispatch_loop as D  # noqa: E402
d = store(0)
D.ROUTING = d
items, rows = D.scoping_items([], lambda m: 6, d=d, live=[("t9", os.getpid())],
                              upcoming=lambda n, **k: (["t11", "t12", "t13"], []))
check("one stand-in per ready task", len(items), 3)
check("one running row per live worker", [r["key"] for r in rows], ["scope:t9"])
_, rows2 = D.scoping_items([{"kind": "scoping", "model": "swift", "key": "scoping: t9"}], lambda m: 6, d=d,
                           live=[("t9", os.getpid())], upcoming=lambda n, **k: ([], []))
check("a worker already running as a manager job is not counted twice", rows2, [])
rca = [{"kind": "rca", "role": "manager", "model": "swift", "key": f"r{i}", "interrupt": False} for i in range(10)]
picked, won = T.round_robin(d, rca + items, {"scoping": 1}, 5)
kinds = {}
for i in picked:
    kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
check("5 free lanes, Scoping holds 1, RCA none: shared fairly", kinds, {"rca": 3, "scoping": 2})
picked, _ = T.round_robin(store(2), rca + items, {"scoping": 1}, 5)
check("Scoping Max lanes 2 with 1 running: only 1 more",
      sum(i["kind"] == "scoping" for i in picked), 1)
sys.exit(1 if FAILED else 0)
