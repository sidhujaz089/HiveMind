#!/usr/bin/env python3
"""t1888 -- one test sweep at a time, across processes.

Measured 2026-10-25 23:29: two `run_tests.py --cache --jobs 2` were alive at once -- pid 1850858 at
3493 s (58 minutes, the hourly one) and a second started beside it. run_tests had an in-process
_PLOCK for its own worker threads and NOTHING across processes, so a second sweep starts happily
while the first runs; they share the CPU, the temp trees and the memory. Two sweeps plus a build is
the 2026-10-24 20:36 configuration in which the box hit 1.42 GB free and the memory guard stopped
Swift mid-conversation.

The rule this suite pins: a sweep takes workspace/run-logs/sweep.lock for its whole life. A second
caller REFUSES (exit 3), names the holder in the holder's own words, and runs nothing. The lock is
an flock, not a pid file, so a killed sweep releases it -- there is no stale lock to clear by hand.

Everything here is seeded: the lock path is passed in or taken from SWEEP_LOCK, and every child
call uses `-k zzz-no-such-suite`, which reaches the suite glob only when the lock is FREE (it then
exits 1, NO SUITES MATCHED) -- so "exit 3" cannot be confused with "the sweep ran".
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import run_tests as rt  # noqa: E402

PASSED = FAILED = 0
CHILD = [sys.executable, str(ROOT / "scripts" / "run_tests.py"), "-k", "zzz-no-such-suite"]


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  (got {got!r}, want {want!r})"))


report("the lock lives under workspace/run-logs/, so a checkout can find it",
       str(rt.SWEEP_LOCK.relative_to(rt.REPO)), "workspace/run-logs/sweep.lock")

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    lock = tmp / "sweep.lock"
    env = {**os.environ, "SWEEP_LOCK_FILE": str(lock)}

    handle = rt.acquire_sweep_lock(label="the test holding it", path=lock)
    held = json.loads(lock.read_text(encoding="utf-8"))
    report("holding it writes the holder's pid and label into the lock file",
           (held.get("pid"), held.get("by")), (os.getpid(), "the test holding it"))
    report("  and when it started", bool(held.get("started")), True)

    r = subprocess.run(CHILD, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180)
    both = r.stdout + r.stderr
    report("a second sweep refuses instead of starting: exit 3", r.returncode, 3)
    report("  the refusal names the holder's pid", str(os.getpid()) in both, True)
    report("  the refusal names the holder's label", "the test holding it" in both, True)
    report("  and it says nothing was started", "nothing was started" in both.lower(), True)

    try:
        rt.acquire_sweep_lock(label="second holder", path=lock)
        busy = None
    except rt.SweepBusy as e:
        busy = str(e)
    report("a second acquisition in one process is refused, not left blocking", bool(busy), True)
    report("  and its message carries the holder's own words", "the test holding it" in (busy or ""), True)

    rt.release_sweep_lock(handle)
    r2 = subprocess.run(CHILD, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180)
    report("after release the same command is no longer refused", r2.returncode != 3, True)
    report("  it reaches the suite selector and finds nothing (exit 1)",
           r2.returncode, 1)

    # THE REASON IT IS AN flock AND NOT A PID FILE: a killed sweep must leave nothing stale.
    holder = tmp / "holder.py"
    holder.write_text(
        "import sys, time\n"
        f"sys.path[:0] = [{str(ROOT / 'scripts')!r}]\n"
        "import run_tests\n"
        f"run_tests.acquire_sweep_lock(label='about to be killed', path={str(lock)!r})\n"
        "print('held', flush=True)\n"
        "time.sleep(120)\n", encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(holder)], stdout=subprocess.PIPE, text=True)
    line = p.stdout.readline().strip()
    os.kill(p.pid, signal.SIGKILL)
    p.wait(timeout=30)
    try:
        after = rt.acquire_sweep_lock(label="after the kill", path=lock)
        free = True
        rt.release_sweep_lock(after)
    except rt.SweepBusy:
        free = False
    report("a SIGKILLed holder releases the lock (nothing stale to clear)",
           (line, free), ("held", True))

    r3 = subprocess.run(CHILD, cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=180)
    report("  and the next sweep is not refused by the dead one's file", r3.returncode, 1)

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
