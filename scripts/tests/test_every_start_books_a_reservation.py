#!/usr/bin/env python3
"""Every start BOOKS a reservation and every finish RELEASES it -- or the gate can never be switched on.

The budget shipped in step 1 able to report and nothing booked: `grep admission_budget dispatch_loop.py` found
only the report line, the ledger file did not exist, and the 20 promised-vs-measured samples step 2 requires could
never accrue. A ledger nobody writes to reads as "nothing promised" for ever, which is the shape of assurance
without the thing -- the same fault as the reaper that existed and was never scheduled.

The holder is derived from the row BOTH sides already carry, so the release cannot drift from the booking: a pid
alone is not enough (one pid can carry two keys, and a re-dispatched lane gets a new pid), which is the same
reason open_dispatches() matches on the pair.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import dispatch_loop as D
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    d = Path(tempfile.mkdtemp())
    budget = d / "budget.jsonl"
    os.environ["HIVEMIND_ADMISSION_BUDGET"] = str(budget)
    os.environ["HIVEMIND_ADMISSION_LEDGER"] = str(budget)
    import importlib
    import admission_budget
    importlib.reload(admission_budget)               # pick up the temp path

    started = {"event": "started", "kind": "production", "chunk": "p1", "key": "k1", "pid": 4242,
               "brief": "b.txt", "model": "m", "at": "2026-10-29T00:00:00Z"}
    check("the holder is the same for a row and its terminal row",
          D.budget_holder(started) == D.budget_holder({**started, "event": "finished"}), D.budget_holder(started))
    check("  and it carries kind, key and pid, not just one of them",
          D.budget_holder(started) == "production:k1:4242", D.budget_holder(started))

    # A BUILD BOOKS HOST BYTES; an inference job books a SLOT.
    D.budget_book(started)
    D.budget_book({"event": "started", "kind": "preflight", "chunk": "p2", "pid": 55})
    rows = [json.loads(l) for l in budget.read_text().splitlines()]
    by = {r["holder"]: r for r in rows if r.get("event") == "reserve"}
    check("a build books host bytes", by["production:k1:4242"]["host_gb"] > 0, str(by.get("production:k1:4242")))
    check("  an inference job books a SLOT and no host bytes",
          by["preflight:p2:55"]["slots"] == 1 and by["preflight:p2:55"]["host_gb"] == 0.0,
          str(by.get("preflight:p2:55")))
    check("  and both carry the pid, so a dead job can be reconciled", by["production:k1:4242"]["pid"] == 4242, "")

    # THE FINISH RELEASES, under the same holder.
    D.record_terminal(started, exit_code=0, seconds=12, result_path="workspace/run-logs/x.log",
                      record=lambda r: None)
    rel = [json.loads(l) for l in budget.read_text().splitlines()]
    released = {r["holder"] for r in rel if r.get("event") == "release"}
    check("a finish RELEASES the promise it made at start", "production:k1:4242" in released, str(released))
    check("  and the budget then reads as nothing promised",
          admission_budget.totals(admission_budget.outstanding()) == (0.0, 0, 0),
          str(admission_budget.outstanding()))

    # A MISSING MODULE NEVER BLOCKS A START.
    sys.modules["admission_budget_missing"] = None
    saved = sys.modules.get("admission_budget")
    sys.modules["admission_budget"] = None
    try:
        ok = D.budget_book(started)
    finally:
        sys.modules["admission_budget"] = saved
    check("a budget that cannot be imported returns None and does NOT raise", ok is None, str(ok))

    src = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    check("the booking is in the START path, after the row that carries the pid",
          "budget_book(row)" in src, "nothing books")
    check("  the release is in the FINISH path", "budget_close(started" in src, "nothing releases")
    print("test_every_start_books_a_reservation:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
