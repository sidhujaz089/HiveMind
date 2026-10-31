#!/usr/bin/env python3
"""The delivery fault: a build that was ACCEPTED and that no branch ever carried.

L4 found nineteen tasks held after one failed attempt; seven were one fault -- a build was accepted
and no branch carried it. On 2026-10-28 the ledger showed 46 tasks that had ever hit it and 32 still
open and never delivered. A re-queue does not fix that: it sends the task back into the same pipeline
in the same state, so a delivery fault fails the same way the second time. The fault has to be VISIBLE
when it happens, which is what this suite pins.

A task that reached `done` (which moves its file to workspace/tasks/done/) or that a LATER accepted run
with a branch delivered is NOT a fault any more, and the baseline may only come down.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import check_workspace as CW
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'BAD'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    def repo(rows, done=(), delivered=None, status=None):
        d = Path(tempfile.mkdtemp())
        (d / "workspace" / "tasks" / "done").mkdir(parents=True)
        (d / "workspace" / "agent-runs.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        for t in done:
            (d / "workspace" / "tasks" / "done" / f"{t}--x.md").write_text("status: done\n")
        for t, st in (status or {}).items():
            (d / "workspace" / "tasks" / f"{t}--x.md").write_text(f"status: {st}\n")
        return d

    anw = lambda t, m="model-a": {"task": t, "outcome": "ACCEPTED-NOT-WRITTEN", "model": m,
                                  "branch": None, "at": "2026-10-01T00:00:00Z"}
    acc = lambda t: {"task": t, "outcome": "ACCEPTED", "model": "model-a",
                     "branch": "coder/brief-x", "at": "2026-10-02T00:00:00Z"}

    d = repo([anw("t1")])
    got = CW.delivery_faults(d)
    check("an accepted build with no branch and a task still open IS a fault",
          got == [("t1", "2026-10-01T00:00:00Z")], str(got))

    d = repo([anw("t1")], done=["t1"])
    check("  a task that reached done is not a fault", CW.delivery_faults(d) == [], str(CW.delivery_faults(d)))

    d = repo([anw("t1"), acc("t1")])
    check("  a task a LATER accepted run delivered is not a fault",
          CW.delivery_faults(d) == [], str(CW.delivery_faults(d)))

    d = repo([anw("t1")], status={"t1": "done"})
    check("  status: done in the live file counts too", CW.delivery_faults(d) == [], str(CW.delivery_faults(d)))

    d = repo([anw("t1", m="faketest")])
    check("  a faketest row is a suite writing the live ledger, NOT a delivery fault",
          CW.delivery_faults(d) == [], str(CW.delivery_faults(d)))

    d = Path(tempfile.mkdtemp())
    check("  an unreadable ledger is None, never 'no faults' -- an absent answer is not zero",
          CW.delivery_faults(d) is None)

    # THE RATCHET: known faults pass, a new one fails, and a recovered one is named for removal.
    d = repo([anw("t1"), anw("t2")])
    (d / "workspace" / "delivery-faults-baseline.json").write_text(
        json.dumps({"known": ["t1"], "count": 1, "at": "x"}), encoding="utf-8")
    live = [t for t, _ in CW.delivery_faults(d)]
    new = [t for t in live if t not in {"t1"}]
    check("the check fails on a live fault that is not on the known list", new == ["t2"], str(live))

    # DRIVE THE METHOD, not just the helper: a ratchet that cannot be read must FAIL, because an
    # unreadable baseline would make every fault look new and an absent one is not zero.
    class Fake:
        def __init__(self, live):
            self.live, self.results = live, []
        def section(self, *a, **k):
            pass
        def result(self, status, name, detail=""):
            self.results.append((status, name, detail))

    f = Fake(d)
    CW.Checker.check_delivery_faults(f)
    check("the method FAILS when the baseline cannot be read",
          bool(f.results) and f.results[0][0] == "FAIL", str(f.results[:1]))
    (d / "workspace" / "delivery-faults-baseline.json").write_text(
        json.dumps({"known": ["t1", "t2"]}), encoding="utf-8")
    f = Fake(d)
    CW.Checker.check_delivery_faults(f)
    check("  and PASSES when every live fault is already known",
          bool(f.results) and f.results[0][0] == "PASS", str(f.results[:1]))

    src = (ROOT / "scripts" / "check_workspace.py").read_text(encoding="utf-8")
    check("the check is DISPATCHED from run(), not merely defined",
          "(self.check_delivery_faults, ())" in src)
    check("  the baseline file is committed with its reasons",
          (ROOT / "workspace" / "delivery-faults-baseline.json").is_file())

    print("test_delivery_faults:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
