#!/usr/bin/env python3
"""There is ONE pressure switch: memory_admit must not keep its own latch under the one R4 changed.

Measured 2026-10-29, and this was my own incomplete fix: R4 demoted pressure to a warning in the tick's decision,
but memory_admit() calls pressure_hold() for itself. So a build could be planned, admitted by its bytes, and then
thrown away by the same stall metric at a lower gate -- observed live: with 21.1 GB free against a 12.1 GB need,
memory_admit refused a start with "waiting for memory pressure: 39% over the last minute", while the same call with
the figure supplied kept it. A second latch under the first is the "four gates" fault this redesign exists to remove.

With the layer ON, pressure annotates here too and the BYTES decide; with it OFF the old refusal is unchanged.
"""
import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    fails = []
    starts = [{"chunk": "p1", "kind": "production", "model": "swift", "brief": "b"}]

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    def run(switch):
        os.environ["HIVEMIND_ADMISSION_GATE"] = switch
        import dispatch_loop
        importlib.reload(dispatch_loop)
        sk = []
        kept, sk2 = dispatch_loop.memory_admit(starts, sk, avail=21.1, pending=0.8)
        return dispatch_loop, kept, sk2

    # 21.1 GB free, 0.8 GB pending, a 8.1 + 4 GB need: the bytes admit it either way.
    d_off, kept_off, _ = run("0")
    d_on, kept_on, _ = run("1")
    check("bytes admit the build with the layer OFF", len(kept_off) == 1, str(len(kept_off)))
    check("  and with it ON", len(kept_on) == 1, str(len(kept_on)))

    # THE LATCH: live mode reads pressure itself, and must consult the same switch.
    src = (ROOT / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
    admit = src[src.index("def memory_admit("):]
    admit = admit[:admit.index("def build_role(")]
    check("memory_admit asks the admission switch", "_bytes_gate = admission_gate_on()" in admit, "")
    check("  and only latches on pressure when that switch is OFF",
          "held = (None if _bytes_gate else pressure_hold())" in admit, admit[:200])
    check("  and says loudly when it annotates instead", "annotated, not refused" in admit, "")

    os.environ.pop("HIVEMIND_ADMISSION_GATE", None)
    importlib.reload(d_off)
    print("test_one_pressure_switch:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
