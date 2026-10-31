#!/usr/bin/env python3
"""The contract for run accounting: every walked item reaches exactly one terminal state.

WHY THIS EXISTS. On 2026-09-07 a 35-item measurement reported 33 rows for 35 items walked.
`scope_queue.py` does `except Exception: continue` and writes NO ROW AT ALL, so the run
forgot two items it had walked. Nothing in the output said so. It was caught only because
Jas remembered clicking a button in a browser tab, and the loss was in the file that had
been repaired that same day.

All nine outside reviewers of draft four independently named the same fix, and it is the
only thing all nine agreed on: the denominator itself must be provable. A bad answer is
visible; a missing record makes the whole dataset look healthier than it is. Several said
this must block any further measurement, because every number from an unreconciled run is
quarantined under this repository's own logic.

So: a manifest is taken BEFORE the run, a row is written on ENTRY (not on success), and the
run reconciles or fails. `assert len(rows) == len(walked)` would have caught both of the
errors draft four found by hand.

THE ORACLE IS WRITTEN HERE AND THE IMPLEMENTATION IS DELEGATED. An agent judged by a suite
it can edit will edit the suite; nine reviewers converged on that. coder.py is given
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


def rows_of(p):
    return [json.loads(line) for line in Path(p).read_text().splitlines() if line.strip()]


def tmp():
    return Path(tempfile.mkdtemp()) / "run.jsonl"


try:
    import runledger
except Exception as e:                                       # noqa: BLE001
    print(f"  [BAD]  scripts/runledger.py does not import: {type(e).__name__}: {e}")
    print("         Required: Ledger(path, run_id, walked, collector_version=...) with")
    print("                   .start(item_id, **fields)   -- write-ahead row, status 'started'")
    print("                   .finish(item_id, status, **fields) -- exactly one terminal row")
    print("                   .reconcile() -> dict(ok, walked, terminal, missing, extra,")
    print("                                        duplicated)")
    print("                   TERMINAL -- the allowed status values, excluding 'started'")
    print("\n0 passed, 1 failed\n")
    sys.exit(1)

Ledger = getattr(runledger, "Ledger", None)
TERMINAL = getattr(runledger, "TERMINAL", None)
check("runledger.Ledger exists", callable(Ledger), True)
check("runledger.TERMINAL exists", TERMINAL is not None, True)
if not callable(Ledger) or TERMINAL is None:
    print(f"\n{PASSED} passed, {FAILED + 1} failed\n")
    sys.exit(1)

print("\nthe status vocabulary covers the failures this repository actually had:")
for st in ("ok", "no-json", "invalid-json", "no-verdict", "ambiguous",
           "http_5xx", "timeout", "error"):
    check(f"TERMINAL includes {st!r}", st in TERMINAL, True)
check("'started' is NOT terminal", "started" in TERMINAL, False)

print("\na clean run reconciles:")
p = tmp()
led = Ledger(p, run_id="r1", walked=["a", "b", "c"])
for i in ("a", "b", "c"):
    led.start(i)
    led.finish(i, "ok")
r = led.reconcile()
check("ok", r["ok"], True)
check("walked", r["walked"], 3)
check("terminal", r["terminal"], 3)
check("missing", list(r["missing"]), [])
check("extra", list(r["extra"]), [])
check("duplicated", list(r["duplicated"]), [])

print("\nTHE 2026-09-07 BUG: an item walked but never finished is caught, not silently lost:")
p = tmp()
led = Ledger(p, run_id="r2", walked=["a", "b", "c"])
led.start("a"); led.finish("a", "ok")
led.start("b")                      # the HTTP 500 -- except: continue, no terminal row
led.start("c"); led.finish("c", "ok")
r = led.reconcile()
check("run is NOT ok", r["ok"], False)
check("the lost item is named", sorted(r["missing"]), ["b"])
check("terminal count is honest", r["terminal"], 2)
check("walked count is from the manifest, not the rows", r["walked"], 3)

print("\na failure is a terminal state, not an absence -- 5xx and timeout still write a row:")
p = tmp()
led = Ledger(p, run_id="r3", walked=["a", "b"])
led.start("a"); led.finish("a", "http_5xx", http_status=500)
led.start("b"); led.finish("b", "timeout", seconds=3600)
r = led.reconcile()
check("a run of nothing but failures still reconciles", r["ok"], True)
check("terminal", r["terminal"], 2)
statuses = sorted(x["status"] for x in rows_of(p) if x["status"] != "started")
check("both failures recorded", statuses, ["http_5xx", "timeout"])

print("\nan item finished twice is a defect, not the last one winning:")
p = tmp()
led = Ledger(p, run_id="r4", walked=["a"])
led.start("a"); led.finish("a", "ok")
try:
    led.finish("a", "error")
except Exception:                                            # noqa: BLE001
    pass                                                     # refusing outright is fine
r = led.reconcile()
check("a double finish does not reconcile clean", r["ok"], False)
check("the duplicate is named", "a" in r["duplicated"] or "a" in r["extra"], True)

print("\nan item that was never in the manifest is extra, not silently accepted:")
p = tmp()
led = Ledger(p, run_id="r5", walked=["a"])
led.start("a"); led.finish("a", "ok")
try:
    led.start("z"); led.finish("z", "ok")
except Exception:                                            # noqa: BLE001
    pass
r = led.reconcile()
check("run is NOT ok", r["ok"], False)
check("the stranger is named", "z" in r["extra"], True)

print("\nan invented status is refused -- the enum is the point:")
p = tmp()
led = Ledger(p, run_id="r6", walked=["a"])
led.start("a")
refused = False
try:
    led.finish("a", "probably_fine")
except Exception:                                            # noqa: BLE001
    refused = True
check("finish() refuses a status outside TERMINAL", refused, True)

print("\nWRITE-AHEAD: the started row is on disk BEFORE the work, so a crash leaves evidence:")
p = tmp()
led = Ledger(p, run_id="r7", walked=["a", "b"])
led.start("a")
on_disk = rows_of(p)                                         # read without finishing
check("the started row is already durable", len(on_disk) >= 1, True)
check("it names the item", on_disk[0]["id"], "a")
check("it is marked started", on_disk[0]["status"], "started")

print("\nevery row carries the provenance that makes it re-derivable:")
p = tmp()
led = Ledger(p, run_id="r8", walked=["a"], collector_version="phase0-2026-09-08")
led.start("a")
led.finish("a", "ok", model="qwen3.8:27b-mtp-bf16", done_reason="stop")
last = rows_of(p)[-1]
check("run_id", last["run_id"], "r8")
check("collector_version", last["collector_version"], "phase0-2026-09-08")
check("caller fields are kept", last["done_reason"], "stop")
check("model identity is kept", last["model"], "qwen3.8:27b-mtp-bf16")
check("every row is timestamped", isinstance(last.get("ts"), str) and len(last["ts"]) > 0, True)

print("\nA TORN LAST LINE still reconciles -- the crash is the case this exists for:")
# Added 2026-09-08 after probing the merged implementation. This was a gap in THIS FILE,
# not in the code it judged: the suite passed 39/0 without ever asking what happens when the
# process dies mid-write. Four reviewers predicted exactly this shape -- a byte-identical
# oracle stops the coding agent editing its test, it does not stop the author writing an
# incomplete one.
#
# The point of writing a row on ENTRY is to survive a crash. If a half-written final line
# makes reconcile() raise, then the tool that should say WHAT WAS LOST is defeated by the
# loss, and we are back to a human remembering a button.
p = tmp()
led = Ledger(p, run_id="r10", walked=["a", "b"])
led.start("a"); led.finish("a", "ok")
led.start("b")
with open(p, "a") as f:
    f.write('{"id": "b", "stat')                            # power cut, mid-write
try:
    r = led.reconcile()
    raised = None
except Exception as e:                                       # noqa: BLE001
    r, raised = None, type(e).__name__
check("reconcile() does not raise on a torn line", raised, None)
if r:
    check("the run is not ok", r["ok"], False)
    check("and the unfinished item is still named", sorted(r["missing"]), ["b"])
    check("the torn line is reported rather than ignored",
          bool(r.get("unreadable_lines")), True)

print("\nreconcile() reads the FILE, not memory -- a fresh reader reaches the same verdict:")
p = tmp()
led = Ledger(p, run_id="r9", walked=["a", "b"])
led.start("a"); led.finish("a", "ok")
led.start("b")                                               # lost
again = Ledger(p, run_id="r9", walked=["a", "b"])            # a second process, same file
r = again.reconcile()
check("the loss is visible to a reader that did not run it", r["ok"], False)
check("and it names the same item", sorted(r["missing"]), ["b"])

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
