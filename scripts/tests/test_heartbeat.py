#!/usr/bin/env python3
"""Liveness comes from the CLOCK, not from what a job says about itself.

WHY IT LIVES HERE TOO. heartbeat.py has a --self-test, and a self-test nothing runs is a
self-test that rots. run_tests.py walks scripts/tests/, so this is what puts it in the suite
session_end.py refuses to finish without.

THE ASSERTION THAT CARRIES THE WEIGHT is the zombie: a beat file whose own contents say
"running", which has stopped being touched, must read STALE. On 2026-09-11 Jas watched two
waiters report work for two hours after their jobs had died -- that is the case this exists
for, and a supervisor that can only tell "file exists" from "file absent" cannot see it.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main():
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "heartbeat.py"), "self-test"],
                       capture_output=True, text=True, timeout=120)
    print(r.stdout.rstrip())
    if r.returncode != 0:
        print("FAIL: heartbeat.py --self-test reported a failure")
        return 1
    # The self-test must actually exercise the stale path. A run where every case was fresh
    # would pass the line above while proving nothing about the only case that matters.
    if "STALE" not in r.stdout:
        print("FAIL: the self-test never produced a STALE verdict")
        return 1
    if "UNREADABLE" not in r.stdout:
        print("FAIL: the self-test never produced an UNREADABLE verdict")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
