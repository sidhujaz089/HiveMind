#!/usr/bin/env python3
"""GATE: session review stops growing the backlog one task per finding (Jas, 2026-10-09:
"The backlog is growing by quite a bit. What is causing that?" -- 249 tasks on 10-07/08).

A second Decide offer from the SAME session, and a second RCA whose fix changes the SAME file,
become NOTES on the open task the first one filed, not new tasks. Different session / file still
files. Hermetic: every path is redirected into a temp dir; taskstore is replaced by a fake."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import session_review as sr

P = F = 0


def check(label, got, want):
    global P, F
    ok = got == want
    P += ok
    F += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f": got {got!r}, want {want!r}"))


class Store:
    def __init__(self):
        self.tasks, self.notes = {}, []

    def add(self, title, body):
        tid = f"t{900 + len(self.tasks)}"
        self.tasks[tid] = body
        return tid

    def open(self, marker, tasks=None):
        return next((t for t, b in self.tasks.items() if marker in b.splitlines()), "")


with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    sr.BASE, sr.FOR_JAS, sr.LEDGER = td, td / "for-jas", td / "ledger.jsonl"
    sr.RCA_DIR, sr.FINDINGS = td / "rca", td / "findings"
    sr.FINDINGS.mkdir()
    st = Store()
    sr._file_for_jas = st.add
    sr._file_task = st.add
    sr._open_group_task = st.open
    sr._note_task = lambda tid, text: st.notes.append((tid, text))

    req = {"harness": "claude", "id": "sess-A"}
    r1 = {"quote": "Shall I build it?", "where": "end"}
    r2 = {"quote": "Want round 3?", "where": "end"}
    sr._for_jas("k1", req, r1)
    sr._for_jas("k2", req, r2)
    sr._for_jas("k3", {"harness": "claude", "id": "sess-B"}, {"quote": "Delete it?", "where": "x"})
    check("two offers from one session file ONE task; another session files its own", len(st.tasks), 2)
    check("the second offer is a note on the first session's task",
          [t for t, _ in st.notes], [next(iter(st.tasks))])

    st2 = Store()
    sr._file_task, sr._open_group_task = st2.add, st2.open
    sr._note_task = lambda tid, text: st2.notes.append((tid, text))

    def finding(key):
        f = sr.FINDINGS / f"{key}.json"
        f.write_text(json.dumps({"key": key, "kind": "missed", "quote": "q", "request": "r"}))
        return f

    def asker_for(path):
        return lambda *a, **k: json.dumps([{"title": "fix it", "kind": "solution", "why": "w",
                                            "change": f"edit {path} so it refuses", "covered_by": ""}])
    sr.rca(finding("f1"), asker=asker_for("scripts/scope_lint.py"))
    sr.rca(finding("f2"), asker=asker_for("scripts/scope_lint.py"))
    sr.rca(finding("f3"), asker=asker_for("scripts/coder_x.py"))
    check("two RCAs fixing the same file file ONE task; a different file files its own", len(st2.tasks), 2)
    check("the second RCA is a note on the first file's task", len(st2.notes), 1)
    check("the task carries its group marker", "GROUP: file:scripts/scope_lint.py" in list(st2.tasks.values())[0], True)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
