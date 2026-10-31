"""t230 for the scheduler: every started job reaches exactly one terminal row. A job whose process
is gone with no finished row is accounted as lost -- once, never against the brief."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dispatch_loop as D

ok = fail = 0
def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}"); ok += bool(cond); fail += not cond

def at(age):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - age))

h = [{"event": "started", "kind": "rca", "chunk": "a.json", "pid": 11, "at": at(3600)},
     {"event": "started", "kind": "rca", "chunk": "b.json", "pid": 12, "at": at(3600)},
     {"event": "finished", "kind": "rca", "chunk": "b.json", "pid": 12, "exit": 0},
     {"event": "started", "kind": "rca", "chunk": "c.json", "pid": 13, "at": at(30)},
     {"event": "started", "kind": "rca", "chunk": "d.json", "pid": 14, "at": at(3600)}]
dead = lambda pid: pid == 14            # only d is still alive
lost = D.lost_jobs(h, alive=dead)
check("a dead job with no finished row is lost", [r["chunk"] for r in lost] == ["a.json"])
check("  a finished one is not", "b.json" not in [r["chunk"] for r in lost])
check("  a just-started one is not (its own row may be on its way)", "c.json" not in [r["chunk"] for r in lost])
check("  a live one is not", "d.json" not in [r["chunk"] for r in lost])
real_alive = D._alive
D._alive = dead
w = D.account_lost(h, write=False)
D._alive = real_alive
check("its terminal row is exit -9, lost, never accepted", [(r["exit"], r["lost"], r["accepted"]) for r in w] == [(-9, True, False)])
check("  and -9 is a yield: it never counts against the brief", -9 in D.YIELD_EXITS)
check("accounted once: with that row written it is no longer lost",
      D.lost_jobs(h + w, alive=dead) == [])
# A SCRIPT REVIEW WHOSE REVIEW WAS WRITTEN is not lost work: take its recorded outcome (2026-10-02).
import truncation_review as TR
real_rows = TR.rows
TR.rows = lambda: [{"path": "s.py", "sha256": "v1", "at": at(1000), "status": "ok"},
                   {"path": "t.py", "sha256": "OTHER", "at": at(1000), "status": "ok"}]
try:
    check("a review row for this version after it started: its outcome is taken",
          D._outcome_elsewhere({"kind": "script-review", "chunk": "s.py", "brief_sha256": "v1", "at": at(2000)}) == "ok")
    check("  a row for a different version is not its outcome",
          D._outcome_elsewhere({"kind": "script-review", "chunk": "t.py", "brief_sha256": "v1", "at": at(2000)}) is None)
    check("  and other kinds have no outcome elsewhere",
          D._outcome_elsewhere({"kind": "rca", "chunk": "s.py", "at": at(2000)}) is None)
finally:
    TR.rows = real_rows
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
