#!/usr/bin/env python3
"""assigned_worker (2026-10-19): an answer missing its OUTCOME line gets ONE format retry, and the
answers of a give-up are kept, not wiped. 32 assignments reached Claude with notes pointing at an
emptied state file. A fake model and a fake task store; nothing live is touched.

Run: python3 scripts/tests/test_assigned_worker_outcome_retry.py
"""
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import assigned_worker as AW  # noqa: E402

D = {"roles": {"jas": {"kind": "person"}, "claude": {"kind": "person"},
               "manager": {"name": "Manager", "kind": "model", "model": "swift", "tasks": []}}}
bad = 0


def check(label, got, want):
    global bad
    bad += got != want
    print(f"  {'ok ' if got == want else 'BAD'} {label}: {got!r}")


def fake_store():
    ts = types.SimpleNamespace(notes=[], meta={"id": "t1", "needs": "manager"})
    ts.find = lambda t: (dict(ts.meta), "")
    ts.add_note = lambda t, text, who=None: ts.notes.append(text)

    def set_meta(t, **k):
        ts.meta.update({kk: v for kk, v in k.items() if kk == "needs"})
        return True
    ts.set_meta = set_meta
    return ts


def run_with(answers):
    ts = fake_store()
    sys.modules["taskstore"] = ts
    calls = []

    def ask(prompt, model, role, tools):
        calls.append(tools)
        return 0, answers[len(calls) - 1]
    AW.prompt_for = lambda *a, **k: "prompt"
    sys.modules["skills"] = types.SimpleNamespace(with_skills=lambda p, *a, **k: (p, []))
    rc = AW.run("t1", "manager", "swift", ask=ask, d=D)
    return rc, ts, calls


with tempfile.TemporaryDirectory() as tmp:
    AW.STATE = Path(tmp)
    rc, ts, calls = run_with(["A long, useful diagnosis with no outcome line.", "OUTCOME: assign claude"])
    check("missing OUTCOME: one tool-free retry asked", calls, [True, False])
    check("  and the retry's line routes it", ts.meta["needs"], "claude")
    check("  and the diagnosis is kept as the note", "useful diagnosis" in ts.notes[-1], True)

    AW.MAX_UNUSABLE = 1
    rc, ts, calls = run_with(["no outcome here", "still nothing"])
    import json
    st = json.loads((Path(tmp) / "t1.json").read_text())
    check("a give-up keeps the answers it points at", st.get("given_up", [[{}]])[-1][-1].get("text"), "no outcome here")
print(f"{'FAILED' if bad else 'ok'}: {bad} bad")
sys.exit(1 if bad else 0)
