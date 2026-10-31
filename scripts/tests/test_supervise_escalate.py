"""supervise.escalate_failure / decide_pending: a failed build no rule explains goes up the org chart
(rework, from the builder's seat) -- no supervisor role, no model (Jas, 2026-10-19)."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import supervise as SV  # noqa: E402
import role_store  # noqa: E402
import dispatch_loop as DL  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


CHART = {"roles": {"jas": {"kind": "person"}, "claude": {"kind": "person"},
                   "manager": {"kind": "model", "model": "swift", "tasks": [{"task": "building"}]},
                   "auditor": {"kind": "model", "model": "q4", "tasks": [{"task": "scoping"}]},
                   "coder": {"kind": "model", "model": "gemma", "tasks": [{"task": "building"}]},
                   "dsh": {"kind": "agent"}},
         "_order": ["jas", "claude", "manager", "auditor", "coder", "dsh"],
         "_escalation": {"vision": 1, "technical": 2, "rework": 3}}
real_load = role_store.load
role_store.load = lambda *a, **k: CHART
try:
    report("the builder's role is found by its model", SV.builder_role("gemma"), "coder")
    report("a model no role builds on has none", SV.builder_role("other"), None)
    report("a role on the model without 'building' still counts as its builder", SV.builder_role("q4"), "auditor")
    report("rank +1: the coder (5) escalates to the auditor (4)", role_store.escalate_up("coder", d=CHART), "auditor")
    report("rank +1: the auditor (4) escalates to the manager (3)", role_store.escalate_up("auditor", d=CHART), "manager")
    report("rank +1: the manager (3) escalates to Claude (2)", role_store.escalate_up("manager", d=CHART), "claude")
    report("an agent seat is skipped, never escalated to", role_store.escalate_up("dsh", d=CHART), "coder")

    notes, metas = [], []

    class Store:
        def add_note(self, tid, text, who=None):
            notes.append((tid, text))

        def set_meta(self, tid, why=None, who=None, **f):
            metas.append((tid, f))
    saved = DL.task_of
    DL.task_of = lambda b: "t42"
    try:
        log = "\n".join(f"line {i}" for i in range(100)) + "\nsomething nobody wrote a rule for"
        to, tid = SV.escalate_failure({"chunk": "p1", "model": "gemma", "at": "T", "exit": 1, "brief": "b.txt"},
                                      log, store=Store(), chart=lambda r: role_store.escalate_up(r, d=CHART))
    finally:
        DL.task_of = saved
    report("a Gemma build goes one rank up, on the build's task", (to, tid), ("auditor", "t42"))
    report("the task is assigned to that seat", metas[0][1].get("needs") if metas else None, "auditor")
    report("the note carries the end of the job log", "something nobody wrote a rule for" in notes[0][1], True)
    report("and says the brief is parked", "parked until it changes" in notes[0][1], True)
    report("ESCALATED parks the brief until it changes",
           SV.is_parked("p1", "s1", [{"decision": "ESCALATED", "chunk": "p1", "brief_sha256": "s1"}]), True)
    report("a changed brief is not parked",
           SV.is_parked("p1", "s2", [{"decision": "ESCALATED", "chunk": "p1", "brief_sha256": "s1"}]), False)
    src = (ROOT / "scripts" / "supervise.py").read_text()
    report("no model is asked for a failed build any more", "ask_q16(prompt)" in src.split("def main")[1], False)
finally:
    role_store.load = real_load
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
