#!/usr/bin/env python3
"""The contract for the scoping run itself: nothing walked is ever lost, and the model is held.

WHY THIS EXISTS, AND WHY IT IS THE ONE THAT MATTERS. `scripts/runledger.py`,
`scripts/evidence.py` and `scripts/modellease.py` were all built and merged on 2026-09-08 and
all pass their own suites. **Not one of them is called by anything.** A mechanism nobody calls
is the same shape as a label nobody enforces, which is precisely what all nine outside
reviewers objected to in draft four. This file is where that stops being true for the run
that started the whole investigation.

The two defects being closed, both still live in `scope_queue.py` until this passes:

  * `except Exception: continue` writes NO ROW. On 2026-09-07 a 35-item run reported 33 rows
    and said nothing; it was caught because Jas remembered clicking a button. Every reviewer
    named this. Several said it must block further measurement.
  * Nothing holds the model. The same run lost those two items to HTTP 500 because a browser
    tab sharing one Ollama was switching models -- thirteen load/unload events inside the run
    window.

THE SHAPE THIS REQUIRES. `main()` cannot be tested without calling a model, so the loop
becomes a function with the model call injected:

    run(tasks, ask, out_path, ledger_path, lease_path=None, model=..., run_id=...) -> dict

`tasks` is a list of (tid, title, body). `ask` is called as `ask(tid, body)` and returns the
raw answer text, or raises. The returned dict is `runledger.Ledger.reconcile()`'s, so the
caller can fail the run on it. `main()` keeps its behaviour and calls `run()` with a real ask.

THE ORACLE IS WRITTEN HERE AND THE IMPLEMENTATION IS DELEGATED. coder.py is given
scripts/scope_queue.py and never this file.
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


def paths():
    d = Path(tempfile.mkdtemp())
    return d / "out.jsonl", d / "ledger.jsonl", d / "lease.json"


def rows(p):
    if not Path(p).exists():
        return []
    return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]


GOOD = '{"id": "%s", "verdict": "NOT_DELEGABLE", "why": "needs Jas"}'
TASKS = [("t1", "one", "body one"), ("t2", "two", "body two"), ("t3", "three", "body three")]

try:
    import scope_queue
except Exception as e:                                       # noqa: BLE001
    print(f"  [BAD]  cannot import scope_queue: {type(e).__name__}: {e}")
    print("\n0 passed, 1 failed\n")
    sys.exit(1)

run = getattr(scope_queue, "run", None)
check("scope_queue.run exists", callable(run), True)
if not callable(run):
    print("         Required: run(tasks, ask, out_path, ledger_path, lease_path=None,")
    print("                       model=..., run_id=...) -> the reconcile() dict.")
    print("         tasks is [(tid, title, body)]; ask(tid, body) -> raw answer text.")
    print(f"\n{PASSED} passed, {FAILED + 1} failed\n")
    sys.exit(1)

print("\nA clean run: every task produces a proposal row AND reconciles:")
out, led, lease = paths()
rep = run(TASKS, lambda tid, body: GOOD % tid, out, led, lease_path=lease, run_id="r1")
check("the run reconciles", rep["ok"], True)
check("walked", rep["walked"], 3)
check("terminal", rep["terminal"], 3)
check("one proposal row per task", len(rows(out)), 3)
check("and they are the right tasks", sorted(r["id"] for r in rows(out)), ["t1", "t2", "t3"])

print("\nTHE 2026-09-07 BUG. A task whose model call RAISES still produces a row:")
out, led, lease = paths()


def boom(tid, body):
    if tid == "t2":
        raise RuntimeError("Ollama returned HTTP 500")
    return GOOD % tid


rep = run(TASKS, boom, out, led, lease_path=lease, run_id="r2")
# `ok` MEANS "every walked item is accounted for". It does NOT mean "every item succeeded",
# and the two must never be conflated -- that distinction is the entire point of the ledger.
# ChatGPT put it best: a bad answer is visible; a missing record makes the whole dataset look
# healthier than it is. So a run with a RECORDED failure reconciles ok=True, and that is a
# success of the accounting, not a whitewash.
#
# An earlier version of this file asserted ok=False here while also asserting missing==[] and
# terminal==3 -- a contradiction. Q16 satisfied it the only way it could, by overwriting
# rep["ok"] after reconcile() returned, which would have made `ok` mean two things at once
# for every future caller. Caught by reading what it said it did.
check("the accounting reconciles BECAUSE the failure was recorded", rep["ok"], True)
check("nothing is missing -- the failure was RECORDED, not dropped", list(rep["missing"]), [])
check("all three reached a terminal state", rep["terminal"], 3)
t2 = [r for r in rows(led) if r["id"] == "t2" and r["status"] != "started"]
check("t2 has exactly one terminal row", len(t2), 1)
check("and its status is a failure, not 'ok'", t2[0]["status"] in ("http_5xx", "error"), True)
check("and the error text is kept", "500" in json.dumps(t2[0]), True)
check("the two successes are still there", len([r for r in rows(out)]), 2)

print("\n`ok` is not overloaded -- it answers accounting, and nothing else:")
# Whether items SUCCEEDED is a different question with a different answer, read off the
# statuses. If a caller wants "did the work go well", it counts them. If it wants "can I
# trust the denominator", it reads ok. One field, one meaning.
out, led, lease = paths()
rep_all_bad = run(TASKS, lambda tid, body: (_ for _ in ()).throw(RuntimeError("nope")),
                  out, led, lease_path=lease, run_id="r2b")
check("a run where EVERY item failed still reconciles", rep_all_bad["ok"], True)
check("because all three were recorded", rep_all_bad["terminal"], 3)
bad = [r for r in rows(led) if r["status"] not in ("started", "ok")]
check("and the failures are visible in the statuses", len(bad), 3)

print("\nThe ledger is WRITE-AHEAD: a started row exists for every task, before its answer:")
out, led, lease = paths()
run(TASKS, lambda tid, body: GOOD % tid, out, led, lease_path=lease, run_id="r3")
started = [r for r in rows(led) if r["status"] == "started"]
check("three started rows", len(started), 3)
check("each names its task", sorted(r["id"] for r in started), ["t1", "t2", "t3"])

print("\nEvery ledger row carries the provenance that makes the run re-derivable:")
out, led, lease = paths()
run(TASKS, lambda tid, body: GOOD % tid, out, led, lease_path=lease,
    model="qwen3.8:27b-mtp-bf16", run_id="r4")
last = [r for r in rows(led) if r["status"] != "started"][-1]
check("run_id", last["run_id"], "r4")
check("collector_version is stamped", bool(last.get("collector_version")), True)
check("the model is recorded", last.get("model"), "qwen3.8:27b-mtp-bf16")

print("\nThe proposal rows are stamped too, so evidence.py can gate them later:")
out, led, lease = paths()
run(TASKS, lambda tid, body: GOOD % tid, out, led, lease_path=lease, run_id="r5")
r0 = rows(out)[0]
# NOT just "the word scope_queue appears". The producer is the key evidence.py uses to ask
# harness_inventory whether the producing script is quarantined, so it has to be a path the
# inventory can actually classify. The looser check passed while status() answered
# "not-applicable" and the gate consulted nothing -- measured 2026-09-08.
check("the producer is a repo-relative script path",
      r0.get("producer"), "scripts/scope_queue.py")
import evidence                                              # noqa: E402
check("and the collector version is one evidence.py recognises",
      evidence.validity(r0["collector_version"]) in ("valid", "superseded"), True)
# The durable assertion is that the gate reaches a REAL VERDICT about these rows, not that
# it lets them through. Once the inventory sees indirect callers (t235), scope_queue.py
# inherits deliberate.py's quarantine and its rows become inadmissible as evidence -- which is
# correct, and stays correct until deliberate.py migrates off the greedy extractor. Asserting
# "passes" here would have made the gate's first true refusal look like a regression.
verdict = evidence.status(r0["producer"])
check("the gate reaches a real verdict about the producer, not 'not-applicable'",
      verdict in ("trusted", "monitored", "quarantined"), True)
try:
    admitted = len(evidence.read_rows(out, caller="test_scope_run.py")) == 3
except evidence.Refused:
    admitted = False
check("and admission follows that verdict rather than being assumed",
      admitted, verdict != "quarantined")

# NOT ASSERTED HERE, AND THE REASON MATTERS. An earlier version of this file demanded
# `evidence.status(producer)` be one of trusted/monitored/quarantined. That is UNSATISFIABLE
# today and Q16 failed five straight attempts against it, correctly: `scope_queue.py` IS NOT
# IN THE HARNESS INVENTORY. The inventory hunts scripts that call a model DIRECTLY, and
# scope_queue.py calls deliberate.deliberate(), which is the one that calls Ollama.
#
# So the script whose run started this entire investigation is invisible to the audit built
# to map the iceberg, and every row it produces answers "not-applicable" at the gate. Two
# reviewers predicted the shape -- ChatGPT: "how do you know there aren't 37? ... indirectly
# calls model wrapper"; Z AI: "inventory the CONSUMERS, not just the callers."
#
# The fix belongs in harness_inventory.py, not here, and it is filed. Until then this suite
# pins the producer STAMP -- which is what scope_queue.py controls -- and does not pretend
# the classification works.
check("a collector_version is recorded", bool(r0.get("collector_version")), True)
check("the raw answer is still kept whole", r0["raw"], GOOD % r0["id"])

print("\nTHE INTERLOCK. While the run is in flight the model is HELD:")
import modellease                                            # noqa: E402
out, led, lease = paths()
seen = {}


def watching(tid, body):
    # What Jas's browser tab was doing on 2026-09-07, at the moment it did it.
    live = modellease.held(lease)
    seen["held"] = live is not None
    seen["model"] = (live or {}).get("model")
    try:
        modellease.check(lease, "gemma4:26b", "load")
        seen["switch_refused"] = False
    except modellease.Held:
        seen["switch_refused"] = True
    return GOOD % tid


run([TASKS[0]], watching, out, led, lease_path=lease, model="q16", run_id="r6")
check("a lease is held while the run is in flight", seen.get("held"), True)
check("and it names the model being measured", seen.get("model"), "q16")
check("an outside model switch is REFUSED mid-run", seen.get("switch_refused"), True)

print("\n...and the lease is RELEASED when the run ends, however it ends:")
check("released after a clean run", modellease.held(lease), None)

out, led, lease = paths()
try:
    run(TASKS, lambda tid, body: (_ for _ in ()).throw(KeyboardInterrupt()), out, led,
        lease_path=lease, model="q16", run_id="r7")
except KeyboardInterrupt:
    pass
check("released even when the run is interrupted", modellease.held(lease), None)

print("\nA run that loses a row REPORTS it -- reconcile is the verdict, not decoration:")
out, led, lease = paths()
rep = run(TASKS, lambda tid, body: GOOD % tid, out, led, lease_path=lease, run_id="r8")
check("a clean run says ok", rep["ok"], True)
led_rows = rows(led)
# Delete one terminal row behind the ledger's back, the way a crash would.
keep = [r for r in led_rows if not (r["id"] == "t3" and r["status"] != "started")]
Path(led).write_text("".join(json.dumps(r) + "\n" for r in keep))
import runledger                                             # noqa: E402
again = runledger.Ledger(led, run_id="r8", walked=[t[0] for t in TASKS]).reconcile()
check("a fresh reader catches the missing row", again["ok"], False)
check("and names it", sorted(again["missing"]), ["t3"])

print("\nThe old silent-drop pattern is gone from the source:")
import re                                                    # noqa: E402
src = (ROOT / "scripts" / "scope_queue.py").read_text(encoding="utf-8")
check("no bare 'except ...: continue' remains around the model call",
      bool(re.search(r"except[^\n]*:\s*\n(\s*)(#[^\n]*\n\s*)*continue\b", src)), False)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
