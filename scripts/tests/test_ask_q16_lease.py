#!/usr/bin/env python3
"""ask_q16 takes the lease of the model it asks, not always the Manager's.

2026-09-30: the Frontline's first Assigned item (t493, gemma4:26b) exited "MODEL LEASE HELD" because
ask_q16 always took workspace/scoping-lease.json -- which a Q4 session review held -- whatever
--model said. Gemma was never going to be asked while Q4 was busy.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import ask_q16 as a  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


W = ROOT / "workspace"
report("the Manager's model keeps the scoping lease (the interlock gate 5 relies on it)",
       a.lease_for("q4", env={}, manager_model="q4"), W / "scoping-lease.json")
report("another model gets a lease of its own", a.lease_for("gemma4:26b", env={}, manager_model="q4"),
       W / "leases" / "gemma4_26b.json")
report("a station's own lease still wins", a.lease_for("gemma4:26b", env={"HIVEMIND_LEASE": "/s.json"},
                                                        manager_model="q4"), Path("/s.json"))
src = (ROOT / "scripts" / "ask_q16.py").read_text()
report("--model sets the lease from the model", "LEASE = lease_for(MODEL)" in src, True)
csrc = (ROOT / "projects" / "local-inference" / "console.py").read_text()
report("console.ask checks the lease of the model it calls (the second place, found on the rerun)",
       "lease_path = modellease.lease_for(name)" in csrc, True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
