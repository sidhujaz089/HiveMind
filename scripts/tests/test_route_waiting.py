#!/usr/bin/env python3
"""dispatch_loop.route_waiting (Jas, 2026-10-08): a parked or held chunk's task is assigned to whoever
it waits on, once; a manual assignment is never overridden; when the chunk is released the task goes
back to production -- only if the scheduler had assigned it. Hermetic: a fake task store."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import dispatch_loop as D  # noqa: E402

FAILED = 0


def check(label, got, want):
    global FAILED
    FAILED += got != want
    print(f"  [{'ok' if got == want else 'BAD'}] {label}: {got!r}")


class Store:
    PRODUCTION = "production"

    def __init__(self):
        self.t = {"t1": {"status": "doing", "needs": "production"},
                  "t2": {"status": "doing", "needs": "jas"},
                  "t3": {"status": "doing", "needs": "production"}}
        self.notes = []

    def find(self, tid):
        return (self.t.get(tid), "")

    def assignee(self, v):
        return v or "production"

    def set_meta(self, tid, needs=None):
        self.t[tid]["needs"] = needs or "production"

    def add_note(self, tid, text, who=""):
        self.notes.append((tid, text))


st = Store()
led = Path(tempfile.mkdtemp()) / "auto.json"
skipped = [("p1-t1-x", "PARKED: 1 attempt(s) in a row came to nothing"),
           ("p2-t2-y", "HELD FOR CLAUDE: preflight objected"),
           ("p3-t3-z", "waiting -- every model has a lane")]
D.route_waiting(skipped, path=led, store=st)
# t1071 (2026-10-14): a 'came to nothing' park is a BRIEF defect, so it goes to the role that owns
# Scoping first (test_route_park_to_scoper.py holds the full contract), not to Claude.
import task_routing as _TR
check("a parked task goes to the scoper first", st.t["t1"]["needs"], _TR.owner(D.ROUTING, "scoping"))
check("a task Jas holds is left with Jas", st.t["t2"]["needs"], "jas")
check("a task only waiting for a lane stays in production", st.t["t3"]["needs"], "production")
D.route_waiting(skipped, path=led, store=st)
check("one note, not one per tick", sum(1 for t, _ in st.notes if t == "t1"), 1)
D.route_waiting([("p1-t1-x", "waiting -- every model has a lane")], path=led, store=st)
check("released: back to production", st.t["t1"]["needs"], "production")
check("Jas's own assignment untouched by the release", st.t["t2"]["needs"], "jas")
sys.exit(1 if FAILED else 0)
