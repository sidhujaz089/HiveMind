#!/usr/bin/env python3
"""Two runs sharing one ledger file must not contaminate each other's reconciliation.

REPRODUCED 2026-09-09, and it is task t251.

reconcile() filters the TAINT scan by run_id and does not filter the TERMINAL scan at all, so
every terminal row in the file counts toward every run:

    L1 = Ledger(path, "r1", ["a"]); L1.start("a"); L1.finish("a", "ok")   -> ok=True, extra=[]
    L2 = Ledger(path, "r2", ["b"]); L2.start("b"); L2.finish("b", "ok")
    L2.reconcile()                                     -> ok=False, extra=["a"]

Run r2 is clean and is reported as having an unaccounted terminal row belonging to r1.

WHY IT IS TIER 1. Gate 1's whole claim is that every walked item writes exactly one terminal
row and the run reconciles. That claim holds today only because each run has used its own
file -- the run_id field exists precisely to permit sharing one. The moment anything does,
reconcile reports a false `extra` and ok=False for a clean run, and a gate that cries wolf gets
overridden.

THE BACKWARD-COMPATIBILITY TRAP, named by the review panel and pinned here because the obvious
fix walks straight into it: a STRICT filter (`row.get("run_id") == self.run_id`) makes every
legacy row invisible, because rows written before run_id was stamped have none. `extra` would
then silently go empty and the ledger would start under-reporting instead of over-reporting --
a quieter version of the same lie. The rule that satisfies both is: a row with NO run_id still
counts; a row with a DIFFERENT run_id does not.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import runledger                                             # noqa: E402

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def fresh():
    return Path(tempfile.mkdtemp()) / "ledger.jsonl"


print("TWO RUNS, ONE FILE. Each must reconcile on its OWN rows:")
p = fresh()
a = runledger.Ledger(p, "r1", ["a"]); a.start("a"); a.finish("a", "ok")
b = runledger.Ledger(p, "r2", ["b"]); b.start("b"); b.finish("b", "ok")
ra, rb = a.reconcile(), b.reconcile()
check("run r1 is clean", (ra["ok"], ra["extra"], ra["missing"]), (True, [], []))
check("run r2 is clean", (rb["ok"], rb["extra"], rb["missing"]), (True, [], []))

print("\nA RUN STILL CATCHES ITS OWN FAULTS -- the filter must not blind it:")
p = fresh()
c = runledger.Ledger(p, "r3", ["x", "y"]); c.start("x"); c.finish("x", "ok")
rc = c.reconcile()
check("an unfinished item of MINE is still missing", rc["missing"], ["y"])
check("and the run is not ok", rc["ok"], False)

p = fresh()
e = runledger.Ledger(p, "r4", ["x"]); e.start("x"); e.finish("x", "ok"); e.finish("x", "ok")
check("a duplicate of MY row is still caught", e.reconcile()["duplicated"], ["x"])

p = fresh()
g = runledger.Ledger(p, "r5", []); g.walked.append("z") if False else None
g2 = runledger.Ledger(p, "r5", []); g2.finish("surprise", "ok")
check("a terminal row of MINE that I never walked is still extra",
      runledger.Ledger(p, "r5", []).reconcile()["extra"], ["surprise"])

print("\nLEGACY ROWS WITH NO run_id STILL COUNT. A strict filter would make them")
print("invisible and turn over-reporting into silent under-reporting:")
p = fresh()
p.write_text(json.dumps({"id": "old", "status": "ok"}) + "\n")
h = runledger.Ledger(p, "r6", [])
check("an unstamped terminal row is still seen as extra", h.reconcile()["extra"], ["old"])

p = fresh()
p.write_text(json.dumps({"id": "old", "status": "ok"}) + "\n")
i = runledger.Ledger(p, "r7", ["old"])
ri = i.reconcile()
check("and it still SATISFIES a walked item", (ri["missing"], ri["ok"]), ([], True))

print("\nTAINT STAYS SCOPED, which already worked and must keep working:")
p = fresh()
j = runledger.Ledger(p, "r8", ["a"]); j.start("a"); j.finish("a", "ok")
j.mark_tainted("me", "evicted mid-run")
k = runledger.Ledger(p, "r9", ["b"]); k.start("b"); k.finish("b", "ok")
check("the tainted run is not ok", j.reconcile()["ok"], False)
check("the other run does not inherit the taint", k.reconcile().get("taint"), None)
check("and the other run is clean", k.reconcile()["ok"], True)

print(f"\n  {PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
