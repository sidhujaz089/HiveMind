#!/usr/bin/env python3
"""The contract for the interlock: a measurement in flight OWNS its model.

NOT TO BE CONFUSED WITH `scripts/models.py lock`, which writes observed DIGESTS so that
sampling resolves without calling Ollama. That is a record of what is on the box. This is a
claim on what may happen to it next. Two different things, and the names are close enough
that a later session will conflate them if nobody says so here.

WHY THIS EXISTS. On 2026-09-07 a two-hour, 35-item measurement lost two items to HTTP 500.
Not the model and not the extractor: Jas was switching models in Open WebUI, which shares one
Ollama with the measurement, the tool server and everything else. Ollama's log shows three
500s and thirteen model load/unload events inside the run window. **A browser tab silently
corrupted a two-hour measurement**, and it was noticed only because he remembered clicking.

Every one of the nine outside reviewers reached for the same word. ChatGPT: "give measurement
runs an exclusive execution lease -- the UI is an interface solution, the lease is a
measurement-integrity solution." Kimi: "no human or browser access to Ollama during batched
runs." Z AI: "pin the model during runs, then FAULT-INJECT: attempt a switch mid-run and
assert refusal."

That last clause is the point of this file. Draft four called the interlock a required
feature and built nothing, and Z AI's line for that was exact -- **"a spec wearing a
measurement's clothes."** DeepSeek: "if you can't simulate it, you can't claim the interlock
is engineered." So this suite is contention, simulated.

THE LEASE MUST EXPIRE. A crashed measurement that holds the model forever is a worse failure
than the one being fixed: it takes the box away from its owner with no way back except
knowing which file to delete. A lease has a deadline and a dead holder loses it.

THE ORACLE IS WRITTEN HERE AND THE IMPLEMENTATION IS DELEGATED. coder.py is given
scripts/modellease.py and never this file.
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


def fresh():
    return Path(tempfile.mkdtemp()) / "lease.json"


try:
    import modellease
except Exception as e:                                       # noqa: BLE001
    print(f"  [BAD]  scripts/modellease.py does not import: {type(e).__name__}: {e}")
    print("         Required:")
    print("           acquire(path, model, purpose, ttl_seconds, holder=None) -> lease dict")
    print("           held(path) -> the live lease, or None if free or expired")
    print("           check(path, model, action) -> None, or raises Held")
    print("           release(path, lease)")
    print("           Held -- the exception, naming holder, purpose and expiry")
    print("\n0 passed, 1 failed\n")
    sys.exit(1)

acquire = getattr(modellease, "acquire", None)
held = getattr(modellease, "held", None)
check_ = getattr(modellease, "check", None)
release = getattr(modellease, "release", None)
Held = getattr(modellease, "Held", None)
for name, obj in (("acquire", acquire), ("held", held), ("check", check_), ("release", release)):
    check(f"modellease.{name} exists", callable(obj), True)
check("modellease.Held is an exception type",
      isinstance(Held, type) and issubclass(Held, Exception), True)
if not all(map(callable, (acquire, held, check_, release))) or not isinstance(Held, type):
    print(f"\n{PASSED} passed, {FAILED + 1} failed\n")
    sys.exit(1)

print("\nAn unheld model is free -- the interlock must not be in the way when nothing is running:")
p = fresh()
check("held() on a fresh path is None", held(p), None)
check("check() permits anything", check_(p, "q16", "load"), None)

print("\nAcquiring names who holds it, why, and until when:")
p = fresh()
lease = acquire(p, model="q16", purpose="35-item scoping re-run", ttl_seconds=7200)
live = held(p)
check("the lease is live", live is not None, True)
check("it names the model", live["model"], "q16")
check("it names the purpose", live["purpose"], "35-item scoping re-run")
check("it has an expiry", "expires" in live, True)
check("it records a holder", bool(live.get("holder")), True)

print("\nFAULT INJECTION: the switch that corrupted the 2026-09-07 run is REFUSED:")
raised = None
try:
    check_(p, "gemma4:26b", "load")                          # Jas, in a browser tab
except Held as e:
    raised = str(e)
check("loading a different model while held raises Held", raised is not None, True)
check("the refusal names what holds it", "35-item scoping re-run" in (raised or ""), True)
check("and says until when", "expires" in (raised or "").lower()
      or ":" in (raised or ""), True)

for action in ("unload", "switch", "stop"):
    raised = None
    try:
        check_(p, "q16", action)
    except Held as e:
        raised = str(e)
    check(f"{action!r} on the HELD model is refused too", raised is not None, True)

print("\n...but the holder's own work is not refused by its own lease:")
check("using the held model is permitted", check_(p, "q16", "use", holder=lease["holder"]), None)

print("\nA second measurement cannot take a model that is already claimed:")
p2 = fresh()
acquire(p2, model="q16", purpose="run A", ttl_seconds=600)
raised = None
try:
    acquire(p2, model="q16", purpose="run B", ttl_seconds=600)
except Held as e:
    raised = str(e)
check("the second acquire is refused", raised is not None, True)
check("and it says who has it", "run A" in (raised or ""), True)

print("\nRELEASE frees it, and the freed model is usable again:")
p3 = fresh()
lz = acquire(p3, model="q16", purpose="run", ttl_seconds=600)
release(p3, lz)
check("held() is None after release", held(p3), None)
check("and a switch is permitted again", check_(p3, "gemma4:26b", "load"), None)

print("\nEXPIRY: a crashed run must not take the box away forever:")
p4 = fresh()
acquire(p4, model="q16", purpose="a run that died", ttl_seconds=-1)   # already past
check("an expired lease is not held", held(p4), None)
check("and it does not block anything", check_(p4, "gemma4:26b", "load"), None)

print("\nA DEAD HOLDER does not keep the model. Liveness, not just a deadline:")
# Three times on 2026-09-08/09 a killed run stranded its lease: `timeout` sends SIGTERM and
# SIGKILL is worse, and neither runs a Python finally block. The TTL is 90 minutes, so a
# crashed job takes the box away for 90 minutes and the next job is correctly refused --
# including, the third time, this repository's own preflight review, which blocked the work
# that was going to fix this.
#
# The holder id already carries the pid ("325556-b8a28794"), so the answer is checkable
# without any new bookkeeping. A lease whose holder no longer exists is not held.
import os                                                    # noqa: E402
p = fresh()
acquire(p, model="q16", purpose="a run that was killed", ttl_seconds=99999)
# a pid that cannot be running, standing in for the process `timeout` killed
data = json.loads(p.read_text())
data["holder"] = "999999999-deadbeef"
p.write_text(json.dumps(data))
check("a lease held by a dead pid is not held", held(p), None)
check("and it blocks nothing", check_(p, "gemma4:26b", "load"), None)
check("so the model can be claimed again", bool(acquire(p, model="q16", purpose="next",
                                                        ttl_seconds=600)), True)

print("\n...but a LIVE holder still holds it, even long past nothing:")
p = fresh()
acquire(p, model="q16", purpose="a live run", ttl_seconds=600)   # held by THIS process
raised = False
try:
    check_(p, "gemma4:26b", "load")
except Held:
    raised = True
check("a lease held by a running process is still enforced", raised, True)

print("\nA corrupt or torn lease file FAILS OPEN, not shut:")
# The same reasoning as the run ledger's torn line. If a half-written lease file wedged
# every model call, the interlock would become the outage. It must be ignorable garbage.
p5 = fresh()
p5.write_text('{"model": "q16", "purp')
check("a torn lease is treated as no lease", held(p5), None)
check("and nothing is blocked by it", check_(p5, "q16", "load"), None)

print("\nThe lease is on disk, so a DIFFERENT PROCESS sees it -- that is the whole point:")
p6 = fresh()
acquire(p6, model="q16", purpose="a measurement", ttl_seconds=600)
import importlib
again = importlib.reload(modellease)                         # a fresh interpreter's view
live = again.held(p6)
check("another process sees the same lease", (live or {}).get("purpose"), "a measurement")
raised = None
try:
    again.check(p6, "gemma4:26b", "load")
except again.Held:
    raised = True
check("and is bound by it", raised, True)

# 2026-10-01: SLOTS. A server decoding 2 requests at once gives 2 leases; the third is refused,
# releasing one frees exactly that slot, and a free slot permits "use".
import modellease as _ml                                     # noqa: E402
_real_slots = _ml.slots
_ml.slots = lambda path, model=None: 2   # t1576: slot_paths may pass the model (t1178)
try:
    ps = Path(tempfile.mkdtemp()) / "lease.json"
    l1 = _ml.acquire(ps, model="q4", purpose="one", ttl_seconds=600)
    l2 = _ml.acquire(ps, model="q4", purpose="two", ttl_seconds=600)
    check("two slots: a second job gets the second slot", (l2.get("path") or "").endswith("lease.slot2.json"), True)
    try:
        _ml.acquire(ps, model="q4", purpose="three", ttl_seconds=600)
        third = False
    except _ml.Held:
        third = True
    check("  a third is refused", third, True)
    _ml.release(ps, l2)
    check("  releasing the second frees ITS file", (ps.parent / "lease.slot2.json").exists(), False)
    check("  and a free slot permits use", _ml.check(ps, "q4", "use") is None, True)
finally:
    _ml.slots = _real_slots

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
