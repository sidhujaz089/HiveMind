#!/usr/bin/env python3
"""GATE 5: the Console must honour the lease, and Jas must always be able to take it back.

`scripts/modellease.py` has worked since 2026-09-08 and the Console has never consulted it.
So a measurement holding a lease is protected from every caller EXCEPT the interface Jas
actually uses -- which is the one that corrupted the 2026-09-07 run.

Jas settled the shape on 2026-09-08, against three reviewers who wanted a neutral proxy: the
interlock lives in OUR stack. And he added the requirement that makes it usable:

  "I want an override though in case I need to interrupt the model. the model/harness needs a
   fail safe to be able to analyze the situation after an interupt and resume or course
   correct."

So refusing is only half. The other half is that a human can always reclaim his own machine,
that the break is RECORDED rather than silent, and that the interrupted run can find out it
was interrupted instead of reporting a clean result.

THE THREE PROPERTIES, in the order they matter:

  1. The Console REFUSES to switch a held model, naming who holds it and why.
  2. Jas can BREAK the lease explicitly, and the break is written down -- a broken lease is
     not a released one, because a released one tells the interrupted run nothing.
  3. A run whose lease was broken under it reports TAINTED, not clean. Silence about an
     interruption is the failure this whole project exists to stop.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def fresh():
    return Path(tempfile.mkdtemp()) / "lease.json"


import modellease                                            # noqa: E402

print("\nThe break is a RECORDED act, not a deleted file:")
brk = getattr(modellease, "break_lease", None)
check("modellease.break_lease exists", callable(brk), True)
if not callable(brk):
    print("         Required: break_lease(path, broken_by, reason) -> the broken lease.")
    print("         A broken lease is NOT a released one: the file survives, marked broken,")
    print("         so the interrupted run can discover what happened to it.")
    print(f"\n{PASSED} passed, {FAILED + 1} failed\n")
    sys.exit(1)

p = fresh()
lease = modellease.acquire(p, model="q16", purpose="a 171-minute measurement", ttl_seconds=9999)
brk(p, broken_by="jas", reason="needed the box")
state = json.loads(p.read_text())
check("the file still exists -- deleting it would erase the evidence", p.is_file(), True)
check("it is marked broken", state.get("status"), "broken")
check("it names who broke it", state.get("broken_by"), "jas")
check("and why", state.get("reason"), "needed the box")
check("and when", "broken_at" in state, True)

print("\nAfter a break the model is FREE -- Jas always gets his machine back:")
check("held() reports nothing held", modellease.held(p), None)
check("and a switch is permitted", modellease.check(p, "gemma4:26b", "load"), None)

print("\nBut the interrupted run can still FIND OUT it was interrupted:")
was = getattr(modellease, "was_broken", None)
check("modellease.was_broken(path, lease) exists", callable(was), True)
if callable(was):
    check("it says the lease was broken", was(p, lease), True)
    p2 = fresh()
    l2 = modellease.acquire(p2, model="q16", purpose="an untroubled run", ttl_seconds=600)
    modellease.release(p2, l2)
    check("a cleanly released lease was NOT broken", was(p2, l2), False)

print("\nA run whose lease was broken must report TAINTED, never clean:")
import runledger                                             # noqa: E402
led = Path(tempfile.mkdtemp()) / "l.jsonl"
lg = runledger.Ledger(led, run_id="r", walked=["a"])
lg.start("a"); lg.finish("a", "ok")
mark = getattr(lg, "mark_tainted", None)
check("Ledger.mark_tainted exists", callable(mark), True)
if callable(mark):
    clean = runledger.Ledger(led, run_id="r", walked=["a"]).reconcile()
    check("an untainted run reports no taint", clean.get("taint", False), False)
    check("and is ok", clean["ok"], True)
    mark("jas", "needed the box")
    rep = runledger.Ledger(led, run_id="r", walked=["a"]).reconcile()
    check("a tainted run says so", rep.get("taint"), True)
    check("names who and why", "jas" in str(rep.get("taint_reason", "")), True)
    check("and is NOT reported ok, however complete the accounting",
          rep["ok"], False)

print("\nThe Console consults the lease at all -- gate 5 is that it stops being blind:")
src = (ROOT / "projects" / "local-inference" / "console.py").read_text(encoding="utf-8")
check("console.py imports modellease", "modellease" in src, True)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
