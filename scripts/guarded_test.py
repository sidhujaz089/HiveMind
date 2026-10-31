#!/usr/bin/env python3
"""Run ONE test command the way the rules said to, because remembering them failed (t9573, 2026-10-30).

Two of the thirteen rules that lived only as prose in the agent files:

  5. Wait on the memory condition before any test or merge-gate run.
  6. One test command at a time, prefixed `timeout 900 nice -n 15`.

On 2026-10-30 I ran suites at PSI 40 twice and recorded it afterwards as a "deviation", because nothing stopped me.
A rule in a sentence is obeyed by whoever read it that morning; a rule in a command is obeyed or it does not run.
This is that command.

    python3 scripts/guarded_test.py python3 scripts/tests/test_x.py
    python3 scripts/guarded_test.py --wait 1800 -- python3 scripts/related_tests.py scripts/foo.py

It WAITS on the condition rather than on a clock, SAYS what it waited for and for how long, takes a lock so only one
guarded run is in flight on this box, and runs the command under `nice` with a timeout. When it gives up it says
SKIPPED and exits 3, so a caller can tell "could not run" from "ran and failed".
"""
import argparse
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

LOCK = Path.home() / ".cache" / "hivemind-locks" / "guarded-test.lock"
DEFAULT_NEED_GIB = 12.0      # the prompt's number: the box must have room before a suite competes with Swift
DEFAULT_MAX_PSI = 10.0       # memory pressure, not a clock
DEFAULT_TIMEOUT = 900
POLL_S = 5


def mem_available_gib(path="/proc/meminfo"):
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return float(line.split()[1]) / 1048576.0
    except (OSError, IndexError, ValueError) as e:                  # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: MemAvailable unreadable ({type(e).__name__}: {e})", file=sys.stderr)
    return None


def psi_avg60(path="/proc/pressure/memory"):
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("some"):
                for field in line.split():
                    if field.startswith("avg60="):
                        return float(field.split("=", 1)[1])
    except (OSError, ValueError) as e:                              # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: memory pressure unreadable ({type(e).__name__}: {e})", file=sys.stderr)
    return None


def gate(free_gib, psi, need_gib=DEFAULT_NEED_GIB, max_psi=DEFAULT_MAX_PSI):
    """(ok, why). A decision, so a suite can test it without this box: unreadable is never a GO."""
    if free_gib is None:
        return False, "host memory is unreadable, so no test can be admitted (UNPROVEN is not a GO)"
    if psi is None:
        return False, "memory pressure is unreadable, so no test can be admitted (UNPROVEN is not a GO)"
    if float(free_gib) < float(need_gib):
        return False, f"{float(free_gib):.1f} GB free is under the {need_gib:g} GB this box needs to itself"
    if float(psi) > float(max_psi):
        return False, f"memory pressure is {float(psi):.1f}, over the {max_psi:g} this box allows"
    return True, ""


def busy(lock=None):
    """True when another guarded run holds the lock. Read-only: it never takes or breaks it."""
    lock = Path(lock or LOCK)
    if not lock.is_file():
        return False
    try:
        fh = open(lock, encoding="utf-8")
    except OSError:
        return False
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True                     # someone holds it
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError as e:                                        # noqa: BLE001 -- said, not swallowed
            # closing the handle releases the lock anyway; say so rather than implying the probe was clean
            print(f"  NOTE: the probe lock did not unlock cleanly ({type(e).__name__}: {e})", file=sys.stderr)
        fh.close()
    return False


def take(lock=None, who=""):
    """The lock file handle, or None when another run holds it. Closing the handle releases it."""
    lock = Path(lock or LOCK)
    lock.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock, "w", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    fh.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} pid={os.getpid()} {who}".strip())
    fh.flush()
    return fh


def wait_for(wait_s=DEFAULT_NEED_GIB and 3600, need_gib=DEFAULT_NEED_GIB, max_psi=DEFAULT_MAX_PSI):
    """(ok, waited_s, why). Waits on the CONDITION; gives up after `wait_s` and says so."""
    started = time.time()
    while True:
        ok, why = gate(mem_available_gib(), psi_avg60(), need_gib, max_psi)
        if ok:
            return True, time.time() - started, ""
        if time.time() - started >= wait_s:
            return False, time.time() - started, why
        print(f"  waiting: {why} (every {POLL_S}s, up to {wait_s}s)", file=sys.stderr)
        time.sleep(POLL_S)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run ONE test command behind the memory gate.")
    ap.add_argument("--wait", type=float, default=3600.0, help="seconds to wait for the condition (default 3600)")
    ap.add_argument("--need-gib", type=float, default=DEFAULT_NEED_GIB)
    ap.add_argument("--max-psi", type=float, default=DEFAULT_MAX_PSI)
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    cmd = [c for c in a.cmd if c != "--"]
    if not cmd:
        ap.error("no command given: pass the test command after --")

    if busy():
        print("  REFUSED: another guarded test is running. One at a time is the rule (t9573 rule 6).")
        return 2
    fh = take(who=" ".join(cmd)[:80])
    if fh is None:
        print("  REFUSED: could not take the lock; another run took it first.")
        return 2
    try:
        ok, waited, why = wait_for(a.wait, a.need_gib, a.max_psi)
        if not ok:
            print(f"  SKIPPED: memory ({why}) after waiting {waited:.0f}s. Record it, do not call it a pass.",
                  file=sys.stderr)
            return 3
        if waited:
            print(f"  waited {waited:.0f}s for memory; now {mem_available_gib():.1f} GB free, "
                  f"pressure {psi_avg60():.1f}", file=sys.stderr)
        started = time.time()
        try:
            r = subprocess.run(["nice", "-n", "15", "timeout", str(int(a.timeout)), *cmd])
        except OSError as e:                                        # noqa: BLE001 -- said, not swallowed
            print(f"  could not run it: {type(e).__name__}: {e}", file=sys.stderr)
            return 127
        print(f"  ran in {time.time() - started:.0f}s (nice 15, timeout {a.timeout:g}s): rc={r.returncode}",
              file=sys.stderr)
        return r.returncode
    finally:
        fh.close()


if __name__ == "__main__":
    sys.exit(main())
