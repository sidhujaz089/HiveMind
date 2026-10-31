#!/usr/bin/env python3
"""t1267: a run that saw an Ollama model slot change mid-run is QUARANTINED (tainted), not
reported ok.

WHY THIS EXISTS. The 2026-09-07 measurement lost two of 35 items to HTTP 500s whose cause was
external contention on a single-slot Ollama: a browser tab (Open WebUI) loaded a model into the
slot the measurement was decoding against, and the run reported healthy rows. The RCA
(workspace/rca/e4edde4201b9.md) put the fix at the schema rung: a run manifest that records the
/api/ps slot-holders before+after each item, so an interfered run CANNOT be stated clean.

The recording half already works: Ledger.start/finish take **fields and persist them, so a
caller can pass resident=["m1"]. The half that is MISSING is in reconcile(): nothing looks at
those snapshots. So a run where every item saw a DIFFERENT resident set -- a model was loaded
and unloaded inside the window -- reconciles ok today. That is the exact lie: a clean report for
a contaminated measurement.

THE RULE reconcile() must learn: the resident snapshots recorded across a run's own rows must
all describe the SAME set of models. Two distinct sets = a load/unload happened in-window = the
run is tainted. Absent snapshots (legacy runs, and runs whose caller records nothing) are NOT
evidence of a change, so they never taint -- a ledger written before this field existed must stay
readable and green.

WHAT THIS SUITE PINS, and why each case is not decoration:
  -- a STABLE set is clean, and a set recorded in a different ORDER is the SAME set (set
     equality, not list equality -- an over-strict fix fails here);
  -- a CHANGED set taints, across items AND within one item's start/finish, and the reason
     NAMES the in-window event (assert the value, not that a key exists);
  -- the two mechanisms COEXIST: a lost row is still named in `missing` while the in-window
     event taints, and a stable-resident lost-row run is NOT given a fabricated in-window taint;
  -- TWO RUNS sharing one ledger file must NOT taint each other (the t251 lesson: a scan that
     reads the whole file instead of this run's rows cries wolf on a clean run, and a gate that
     cries wolf gets bypassed).

THE ORACLE IS WRITTEN HERE AND THE IMPLEMENTATION IS DELEGATED. coder.py is given
scripts/runledger.py and never this file.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def tmp():
    return Path(tempfile.mkdtemp()) / "ledger.jsonl"


try:
    import runledger
except Exception as e:                                       # noqa: BLE001
    print(f"  [BAD]  scripts/runledger.py does not import: {type(e).__name__}: {e}")
    print("         Required: Ledger(path, run_id, walked) with .start(id, **fields),")
    print("                   .finish(id, status, **fields), .reconcile() -> dict(ok, ...).")
    print("         resident=[...] is recorded via **fields; reconcile() must taint a run")
    print("         whose recorded resident snapshots are not all the SAME set.")
    print("\n0 passed, 1 failed\n")
    sys.exit(1)

Ledger = getattr(runledger, "Ledger", None)
if not callable(Ledger):
    print(f"  [BAD]  runledger.Ledger is not callable\n\n{PASSED} passed, {FAILED + 1} failed\n")
    sys.exit(1)

print("\nLEGACY RUNS (no resident recorded anywhere) stay clean --")
print("a ledger written before this field existed is not retroactively quarantined:")
p = tmp()
led = Ledger(p, run_id="legacy1", walked=["a", "b"])
led.start("a"); led.finish("a", "ok")
led.start("b"); led.finish("b", "ok")
r = led.reconcile()
check("a legacy run reconciles ok", r["ok"], True)
check("and carries no in-window taint", r.get("taint"), None)

print("\nA STABLE resident set across the run is clean:")
p = tmp()
led = Ledger(p, run_id="stable1", walked=["a", "b"])
led.start("a", resident=["m1"]); led.finish("a", "ok", resident=["m1"])
led.start("b", resident=["m1"]); led.finish("b", "ok", resident=["m1"])
r = led.reconcile()
check("a stable-set run reconciles ok", r["ok"], True)
check("and is not tainted", r.get("taint"), None)

print("\nTHE SAME SET IN A DIFFERENT ORDER IS NOT AN IN-WINDOW EVENT")
print("(set equality, not list equality -- a fix that compares lists fails here):")
p = tmp()
led = Ledger(p, run_id="order1", walked=["a", "b"])
led.start("a", resident=["m1", "m2"]); led.finish("a", "ok", resident=["m1", "m2"])
led.start("b", resident=["m2", "m1"]); led.finish("b", "ok", resident=["m1", "m2"])
r = led.reconcile()
check("a reordered set is clean", r["ok"], True)
check("and is not tainted", r.get("taint"), None)

print("\nA resident set that CHANGED between items is an in-window load/unload:")
p = tmp()
led = Ledger(p, run_id="change1", walked=["a", "b"])
led.start("a", resident=["m1"]); led.finish("a", "ok", resident=["m1"])
led.start("b", resident=["m2"]); led.finish("b", "ok", resident=["m2"])
r = led.reconcile()
check("a run with an in-window event is NOT ok", r["ok"], False)
check("and it is quarantined (tainted)", r.get("taint"), True)
check("and the reason names the in-window event",
      "in-window" in str(r.get("taint_reason", "")), True)

print("\nA single item whose start and finish saw different slots is the same event:")
p = tmp()
led = Ledger(p, run_id="change2", walked=["a"])
led.start("a", resident=["m1"]); led.finish("a", "ok", resident=["m2"])
r = led.reconcile()
check("one item spanning a load/unload is NOT ok", r["ok"], False)
check("and it is quarantined (tainted)", r.get("taint"), True)

print("\nA FULLY RECORDED run that changed slots reports nothing missing (taint is")
print("additive -- it does not fake a lost row):")
p = tmp()
led = Ledger(p, run_id="both1", walked=["a", "b"])
led.start("a", resident=["m1"]); led.finish("a", "ok", resident=["m1"])
led.start("b", resident=["m2"]); led.finish("b", "ok", resident=["m2"])
r = led.reconcile()
check("a fully-recorded run has nothing missing", r["missing"], [])

print("\nTHE IN-WINDOW TAINT COEXISTS WITH A LOST ROW -- taint must not")
print("swallow the row-loss report (RCA: rows<walked is a separate, additive reason):")
p = tmp()
led = Ledger(p, run_id="both2", walked=["a", "b"])
led.start("a", resident=["m1"]); led.finish("a", "ok", resident=["m1"])
led.start("b", resident=["m1"])                              # lost: no terminal row
r = led.reconcile()
check("the lost item is still named", sorted(r["missing"]), ["b"])
check("and the run is not ok", r["ok"], False)
check("a lost row alone does NOT fabricate an in-window taint", r.get("taint"), None)

print("\nTWO RUNS SHARING ONE LEDGER FILE MUST NOT TAINT EACH OTHER")
print("(the t251 lesson: scan THIS run's rows, not the whole file):")
p = tmp()
a = Ledger(p, run_id="runA", walked=["x"])
a.start("x", resident=["m1"]); a.finish("x", "ok", resident=["m1"])
b = Ledger(p, run_id="runB", walked=["y"])
b.start("y", resident=["m2"]); b.finish("y", "ok", resident=["m2"])
ra, rb = a.reconcile(), b.reconcile()
check("run A is clean despite run B's different slot in the same file",
      (ra["ok"], ra.get("taint")), (True, None))
check("run B is clean despite run A's slot in the same file",
      (rb["ok"], rb.get("taint")), (True, None))

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
