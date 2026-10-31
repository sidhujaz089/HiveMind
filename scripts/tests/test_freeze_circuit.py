#!/usr/bin/env python3
"""The freeze circuit is the Manager's (Jas, 2026-10-01): it files the task, applies the decision,
and only verdicts after a reset count. Fakes for the task store and notify -- never the live ones."""
import json
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
fails = []


def ck(label, ok):
    print(f"  [{'ok' if ok else 'BAD'}]  {label}")
    if not ok:
        fails.append(label)


class FakeTS:
    def __init__(self):
        self.tasks, self.notes, self.meta = {}, [], {}

    def ordered(self):
        return [({"id": k, "title": v}, "") for k, v in self.tasks.items()]

    def find(self, tid):
        return ({"id": tid, "title": self.tasks[tid]}, "") if tid in self.tasks else (None, "")

    def add(self, title, area, **kw):
        tid = f"t{900 + len(self.tasks)}"
        self.tasks[tid] = title
        return tid

    def add_note(self, tid, text, who=""):
        self.notes.append((tid, text))

    def set_status(self, tid, st):
        self.meta[(tid, "status")] = st

    def set_meta(self, tid, **kw):
        self.meta.update({(tid, k): v for k, v in kw.items()})


def main():
    import freeze_circuit as FC
    import bridge_trigger as BT
    tmp = Path(tempfile.mkdtemp())
    FC.RESET = tmp / "reset.json"
    ts = FakeTS()
    FC._ts = lambda: ts
    sent = []
    sys.modules["notify"] = types.SimpleNamespace(send=lambda *a, **k: sent.append(a))

    led = tmp / "scope.jsonl"
    rows = [{"stage": "merge", "at": f"2026-09-30T0{i}:00:00Z", "class": "oracle-weak", "task": f"t{i}"}
            for i in range(3)]
    led.write_text("".join(json.dumps(r) + "\n" for r in rows))
    ck("three weak-oracle verdicts switch the freeze off", BT.freeze_enabled(ledger=led)[0] is False)
    FC.RESET.write_text(json.dumps({"at": "2026-09-30T01:30:00Z"}))
    ck("a reset between them leaves only the later ones counted (2 < 3: on)",
       BT.freeze_enabled(ledger=led)[0] is True)
    FC.RESET.unlink()

    real_fe = BT.freeze_enabled
    BT.freeze_enabled = lambda: (False, "3 of the last 3 merge verdicts sent work back for an oracle defect")
    try:
        t1 = FC.ensure()
        ck("ensure files ONE task for the Manager", t1 in ts.tasks and ts.tasks[t1] == FC.TITLE)
        ck("and a second tick does not file another", FC.ensure() == t1 and len(ts.tasks) == 1)
        BT.freeze_enabled = lambda: (False, "switched off (bridge-freeze-OFF exists)")
        ts.tasks.clear()
        ck("a MANUAL off switch is not the Manager's to undo", FC.ensure() is None)
    finally:
        BT.freeze_enabled = real_fe

    ts.tasks["t950"] = FC.TITLE
    ans = tmp / "a.md"
    ans.write_text('RCA...\n```freeze-decision\n{"reset": true, "why": "gates now catch shape-only oracles"}\n```\n')
    ck("reset: task done", FC.answer("t950", ans) == ("done", ""))
    ck("  and the reset is recorded", FC.last_reset().get("why") == "gates now catch shape-only oracles")
    ck("  and the verdicts before the reset no longer count -- the freeze is back on",
       BT.freeze_enabled(ledger=led)[0] is True)
    ans.write_text('```freeze-decision\n{"reset": false, "why": "x", "needs_jas": "keep 3 or 5?"}\n```')
    ck("needs_jas: routed to Jas", FC.answer("t950", ans) == ("doing", "jas")
       and ts.meta.get(("t950", "needs")) == "jas")
    ck("  and his phone is notified", bool(sent) and sent[-1][0] == "needs-you")
    ans.write_text('```freeze-decision\n{"reset": false, "why": "x", "fix": "tighten scope_lint"}\n```')
    ck("fix: stays the Manager's", FC.answer("t950", ans) == ("doing", ""))
    ans.write_text("no block at all")
    ck("no block: nothing decided", FC.answer("t950", ans) is None)
    src = (ROOT / "scripts" / "bridge_trigger.py").read_text(encoding="utf-8")
    ck("the switch counts only verdicts after the last reset", "freeze_circuit.last_reset()" in src)
    for f in fails:
        print("FAIL:", f)
    print("test_freeze_circuit:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
