#!/usr/bin/env python3
"""The admission gate is OFF by default, proves BOTH states, and refuses when it cannot know.

Redesign step 2 (workspace/rca/scheduler-audit-2026-10-29.md). The gate changes behaviour, so it ships switched
OFF and is switched on only when the numbers exist: GATE_NEEDS_SAMPLES = 20 builds of promised-vs-measured data,
because what the gate must know is how often a build's real peak exceeds its 12 GB booking -- and with 20 recorded
builds the largest single peak is already visible.

The rule it enforces is per RESOURCE, because a review and a measurement settled that they are different: a BUILD
needs host bytes (12 + 4 GB), an INFERENCE job needs a sequence slot inside the engine's preallocated pool.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import capacity_model as C
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    # OFF BY DEFAULT, and off on any doubt.
    check("no switch set -> OFF", C.gate_switch_on(env={}, path="/nonexistent") is False, "")
    check("  an explicit env value turns it on", C.gate_switch_on(env={C.GATE_ENV: "1"}, path="/nope") is True, "")
    check("  a switch FILE turns it on", C.gate_switch_on(env={}, path=str(ROOT / "scripts" / "capacity_model.py"))
          is True, "")
    check("  a junk value does not", C.gate_switch_on(env={C.GATE_ENV: "maybe"}, path="/nope") is False, "")

    # OFF MEANS UNCHANGED BEHAVIOUR -- the whole reason for shipping it dark.
    allow, why = C.admission_gate("production", 1.0, 16.0, on=False)
    check("gate OFF: everything allowed, exactly as before", allow is True and "gate off" in why, why)

    # ON: judged on the budget, both directions.
    allow, why = C.admission_gate("production", 5.0, 16.0, on=True)
    check("gate ON, 5 GB free: a 16 GB build is REFUSED, naming both numbers",
          allow is False and "16 GB" in why and "5.0 GB" in why, why)
    allow, why = C.admission_gate("production", 21.4, 16.0, on=True)
    check("gate ON, 21.4 GB free: allowed", allow is True and "21.4 GB" in why, why)
    allow, why = C.admission_gate("production", None, 16.0, on=True)
    check("  unreadable memory REFUSES with UNPROVEN, never allows", allow is False and "UNPROVEN" in why, why)

    # INFERENCE IS A SLOT, NOT BYTES.
    allow, why = C.admission_gate("preflight", 100.0, 16.0, slots_free=0, slots_max=4, on=True)
    check("gate ON: an inference job with no free slot is refused even with 100 GB free",
          allow is False and "sequence slot" in why, why)
    allow, why = C.admission_gate("preflight", 1.0, 16.0, slots_free=2, slots_max=4, on=True)
    check("  and allowed on a slot even with almost no host memory", allow is True and "slot" in why, why)
    allow, why = C.admission_gate("preflight", 10.0, 16.0, on=True)
    check("  an unreadable SLOT count refuses too", allow is False and "UNPROVEN" in why, why)

    check("the N is stated and is a number", C.GATE_NEEDS_SAMPLES == 20, str(C.GATE_NEEDS_SAMPLES))

    # AND IT IS WIRED, or it is just a function.
    src = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    check("launch() consults the gate", "admission gate:" in src, "the gate is not wired into the launch path")
    check("  and an ON switch with an unreadable model REFUSES rather than passing",
          "refusing rather than guessing" in src, "")
    print("test_admission_gate_off_by_default:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
