#!/usr/bin/env python3
"""lane_status's MANAGER QUEUE: every job on the manager's plate, with done/total and an ETA."""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import lane_status as LS  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    now = time.time()
    day = int(now % 86400)
    hhmmss = lambda s: time.strftime("%H:%M:%S", time.gmtime(s % 86400))  # noqa: E731
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "in").mkdir(); (t / "out").mkdir()
        for n in ("a-job", "b-job", "c-job", "d-job"):
            (t / "in" / f"{n}.txt").write_text("prompt")
        (t / "out" / "a-job.md").write_text("answer")
        (t / "out" / "b-job.md").write_text("")            # empty = not done
        log = [f"{hhmmss(day - 3000)}Z START a-job (task t1)", f"{hhmmss(day - 1800)}Z END a-job rc=0",
               f"{hhmmss(day - 1800)}Z START b-job (task t2)", f"{hhmmss(day - 600)}Z END b-job rc=0",
               f"{hhmmss(day - 600)}Z START c-job (task t3)"]
        (t / "progress.log").write_text("\n".join(log) + "\n")
        ptr = t / "q.json"
        ptr.write_text(json.dumps({"in": str(t / "in"), "out": str(t / "out"),
                                   "progress": str(t / "progress.log")}))
        q = LS.scoping_queue(ptr, now=now)
    states = {j["job"]: j["state"] for j in q["jobs"]}
    report("done / running / pending per job", states,
           {"a-job": "done", "b-job": "pending", "c-job": "running", "d-job": "pending"})
    report("an empty answer is not done", states["b-job"], "pending")
    report("done/total", (q["done"], q["total"]), (1, 4))
    report("median of finished job times (1200, 1200)", q["median_s"], 1200)
    report("ETA = 2 pending x 1200 + running remainder (1200-600), whole seconds",
           abs(q["eta_s"] - 3000) <= 2, True)
    report("no pointer -> nothing shown", LS.scoping_queue(Path(tmp) / "none.json"), None)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
