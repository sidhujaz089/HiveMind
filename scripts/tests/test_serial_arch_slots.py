#!/usr/bin/env python3
"""A model Ollama runs serially has ONE slot, whatever OLLAMA_NUM_PARALLEL says (2026-10-02).

Jas raised both servers to OLLAMA_NUM_PARALLEL=4; the scheduler and the leases read 4, but Ollama
loaded Qwen with '-np 1': 'model architecture does not currently support parallel requests
architecture=qwen35'. Station 2 then ran three RCAs and scoping at once on a one-slot server --
each queued inside Ollama, taking up to 4x its time against a timeout that counts toward give-up.

  * a serial architecture -> 1, on any lease, the model passed or read from the lease's role
  * a parallel one keeps the unit's number
  * a model whose architecture is unknown keeps the unit's number (unknown is not serial)
  * the scheduler's model_slots goes through the same rule

Stubs only: no journal, no model server, no unit is read.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import modellease as M  # noqa: E402

fails = []


def ck(label, got, want):
    print(f"  [{'ok' if got == want else 'FAIL'}]    {label} -> {got!r}")
    if got != want:
        fails.append(f"{label}: expected {want!r}, got {got!r}")


def main():
    real = (M._unit_slots, M.serial_archs, M.model_arch, M._lease_model)
    try:
        M._unit_slots = lambda p: 4
        M.serial_archs = lambda: {"qwen35"}
        arch = {"qwen-q4": "qwen35", "gemma": "gemma4"}
        M.model_arch = lambda m: arch.get(m)
        M._lease_model = lambda p: "qwen-q4" if p.name == "scoping-lease-station2.json" else None
        ck("serial architecture, model passed: 1", M.slots(Path("x/leases/q.json"), model="qwen-q4"), 1)
        ck("serial architecture, model read from the lease's role: 1",
           M.slots(Path("workspace/scoping-lease-station2.json")), 1)
        ck("  and its lease has one slot file", len(M.slot_paths(Path("workspace/scoping-lease-station2.json"))), 1)
        ck("parallel architecture keeps the unit's 4", M.slots(Path("x/leases/g.json"), model="gemma"), 4)
        ck("unknown architecture keeps the unit's 4", M.slots(Path("x/leases/u.json"), model="other"), 4)
        import dispatch_loop as D
        M.lease_for = lambda m, **k: Path("x/leases/any.json")
        ck("the scheduler's model_slots uses the same rule", (D.model_slots("qwen-q4"), D.model_slots("gemma")),
           (1, 4))
    finally:
        M._unit_slots, M.serial_archs, M.model_arch, M._lease_model = real

    for x in fails:
        print("FAIL:", x)
    print("test_serial_arch_slots:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
