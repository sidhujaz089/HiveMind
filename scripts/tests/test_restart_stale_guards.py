#!/usr/bin/env python3
"""t1153 step 5: the memory and ThinkPad guards are restart-safe watchers, so a stale one is restarted
rather than left red for session end -- except memory-guard inside a dip, where a fresh (armed)
process would trip a second time."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import memory_guard  # noqa: E402
import restart_stale as R  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


report("memory-guard is restart-safe", "memory-guard.service" in R.SAFE, True)
report("thinkpad-guard is restart-safe", "thinkpad-guard.service" in R.SAFE, True)
report("every SAFE entry points at a real file", all(p.is_file() for p in R.SAFE.values()), True)
low = memory_guard.RESERVE_GB + 1
report("memory-guard is held inside a dip", bool(R.hold_restart("memory-guard.service", low)), True)
report("memory-guard restarts with room", R.hold_restart("memory-guard.service", memory_guard.RESERVE_GB + 10), "")
report("other units are never held", R.hold_restart("thinkpad-guard.service", 0.1), "")
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
