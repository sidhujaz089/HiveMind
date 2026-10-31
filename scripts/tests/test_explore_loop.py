#!/usr/bin/env python3
"""The exploration gate must REFUSE a further leg, not only grant one.

WHY IT LIVES HERE TOO. explore_loop.py has a --self-test, and a self-test nothing runs is a
self-test that rots. run_tests.py walks scripts/tests/, so this is what makes the gate part of
the suite session_end.py refuses to finish without.

THE ASSERTION THAT MATTERS is not that a concrete path is granted a leg -- it is that a vague
one, a repeat, and an explicit NONE are all refused. A gate that only ever says yes is the
"looked healthy the whole time" failure: the first probe run spent 51 of 61 rounds off-brief
and nothing objected.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main():
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "explore_loop.py"),
                        "--self-test"], capture_output=True, text=True, timeout=120)
    print(r.stdout.rstrip())
    if r.returncode != 0:
        print("FAIL: explore_loop.py --self-test reported a failure")
        return 1
    # The self-test must actually have refused something. A run that granted every case and
    # still exited 0 would pass the line above while proving nothing.
    if "-> False" not in r.stdout:
        print("FAIL: the self-test granted every case -- the gate never refused")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
