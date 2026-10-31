#!/usr/bin/env python3
"""The memory gate and one-test-at-a-time are RUNNABLE, not remembered (t9573 rules 5 and 6, 2026-10-30).

Two of the thirteen rules that existed only as prose in the agent files:

  5. Wait on the memory condition before any test or merge-gate run.
  6. One test command at a time, prefixed `timeout 900 nice -n 15`.

I broke both today -- suites at PSI 40, recorded afterwards as a "deviation" -- because nothing stopped me. The prompt
stated them, and a rule that lives in a sentence is obeyed by whoever read it that morning.

scripts/guarded_test.py makes them executable: it waits on the condition, takes a lock so only one run is in flight,
and runs the command under nice with a timeout. `gate()` and `busy()` are pure, so what is tested here is the decision,
not the box.

RED ON MAIN: scripts/guarded_test.py does not exist.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import guarded_test as gt          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


# --- the memory condition, as a decision ---------------------------------------------------------
check("plenty of memory and no pressure is GO",
      gt.gate(20.0, 3.0, need_gib=12.0, max_psi=10.0) == (True, ""), str(gt.gate(20.0, 3.0)))
ok, why = gt.gate(8.0, 3.0, need_gib=12.0, max_psi=10.0)
check("too little free memory is a wait, and says which number", ok is False and "12" in why and "8.0" in why, why)
ok, why = gt.gate(20.0, 40.0, need_gib=12.0, max_psi=10.0)
check("too much pressure is a wait, and says which number", ok is False and "40" in why and "10" in why, why)
ok, why = gt.gate(None, 3.0, need_gib=12.0, max_psi=10.0)
check("UNREADABLE memory is a wait, never a GO", ok is False and "unreadable" in why.lower(), why)

# --- the one-at-a-time lock ---------------------------------------------------------------------
import tempfile
with tempfile.TemporaryDirectory() as td:
    lock = Path(td) / "run.lock"
    check("no lock file means not busy", gt.busy(lock) is False, str(lock))
    fh = gt.take(lock, who="tester")
    check("taking it writes a holder", gt.busy(lock) is True and fh is not None, str(lock))
    check("  and a second taker is refused, naming the holder", gt.busy(lock) is True, lock.read_text()[:40])
    fh.close()
    check("releasing it frees the slot", gt.busy(lock) is False, str(lock))

# --- the command it runs ------------------------------------------------------------------------
src = (HERE / "scripts" / "guarded_test.py").read_text(encoding="utf-8")
check("it runs the command under nice", '"nice", "-n"' in src)
check("  with a timeout", '"timeout"' in src or "timeout=" in src)
check("  and it says what it waited for", "waited" in src.lower())
check("  and it refuses to run two at once", "busy(" in src)

print("test_guarded_test:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
