#!/usr/bin/env python3
"""The home for the two checks a close-out was doing for no reason (2026-10-30).

Jas: "I want to only do what's necessary and move other routines out of session end where possible."

The corpus index already had `corpus-index.timer`. The app pipeline (which shells out to `apksigner` for every
published APK, emulated on this box, L-080) and the watched-corpus digests had no home at all, so they lived in
`session_end.py` -- up to 600 s each, paid on every close, including the close that is already ten minutes into its
full workspace check.

This is their home: one scheduled run that does both and WRITES DOWN what it found. A scheduled check that fails
silently is worse than no check, because the absence of a line reads as health -- so every run appends a row with
its exit code, its duration, and the tail of its output.

    python3 scripts/slow_checks.py            # run both, append rows, print a line each
    python3 scripts/slow_checks.py --check apps

`session_end.py` asks `routine_decision()`, which reads this job's own record: a fresh record means the close-out
leaves it alone; a record that is behind (or missing) means the close-out runs it after all. Unknown is not fresh.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
LEDGER = REPO / "workspace" / "slow-checks.jsonl"
TIMEOUT = 1200.0                      # per check; the app pipeline is the slow one (apksigner, emulated)

# The checks, and the command each one runs. A dict rather than a list of lambdas so `--check <name>` can name one.
CHECKS = {
    "apps": [sys.executable, str(SCRIPTS / "check_apps.py")],
    "digests": [sys.executable, str(SCRIPTS / "digests.py")],
}


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def run_one(argv, timeout=TIMEOUT):
    """(rc, seconds, tail). A timeout is an rc and a word, never an exception the caller has to know about."""
    started = time.time()
    try:
        r = subprocess.run(argv, cwd=str(REPO), capture_output=True, text=True, errors="replace",
                           timeout=timeout, stdin=subprocess.DEVNULL)
        tail = ((r.stdout or "") + (r.stderr or "")).strip()[-600:]
        return r.returncode, round(time.time() - started, 2), tail
    except subprocess.TimeoutExpired:
        return 124, round(time.time() - started, 2), f"timeout: no answer in {timeout:g}s"
    except OSError as e:                                            # noqa: BLE001 -- said, not swallowed
        return 127, round(time.time() - started, 2), f"could not run it: {type(e).__name__}: {e}"


def run_all(ledger=None, checks=None):
    """Run each check, append one row each, print one line each. {ran, failed}.

    `checks` is [(name, callable)] so a suite can drive this without starting apksigner. The callable returns a
    CompletedProcess-shaped object or raises TimeoutExpired; both are recorded.
    """
    ledger = Path(ledger or LEDGER)
    checks = checks if checks is not None else [(name, (lambda argv=argv: subprocess.run(
        argv, cwd=str(REPO), capture_output=True, text=True, errors="replace", timeout=TIMEOUT,
        stdin=subprocess.DEVNULL))) for name, argv in CHECKS.items()]
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ran, failed = 0, 0
    with open(ledger, "a", encoding="utf-8") as out:
        for name, fn in checks:
            started = time.time()
            try:
                r = fn()
                rc = int(getattr(r, "returncode", 0))
                tail = (((getattr(r, "stdout", "") or "") + (getattr(r, "stderr", "") or ""))).strip()[-600:]
            except subprocess.TimeoutExpired:
                rc, tail = 124, "timeout: no answer"
            except OSError as e:                                    # noqa: BLE001 -- said, not swallowed
                rc, tail = 127, f"could not run it: {type(e).__name__}: {e}"
            seconds = round(time.time() - started, 2)
            row = {"at": _now(), "check": name, "rc": rc, "seconds": seconds, "tail": tail}
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            ran += 1
            failed += bool(rc)
            print(f"  [{'ok' if not rc else 'FAIL'}] {name} (rc={rc}, {seconds}s)")
    return {"ran": ran, "failed": failed}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the checks a close-out used to run.")
    ap.add_argument("--check", action="append", choices=sorted(CHECKS),
                    help="run only this check (repeatable)")
    args = ap.parse_args(argv)
    names = args.check or list(CHECKS)
    checks = [(n, (lambda argv=CHECKS[n]: subprocess.run(
        argv, cwd=str(REPO), capture_output=True, text=True, errors="replace", timeout=TIMEOUT,
        stdin=subprocess.DEVNULL))) for n in names]
    rows = run_all(checks=checks)
    print(f"  {rows['ran']} check(s), {rows['failed']} failing -- recorded in {LEDGER.relative_to(REPO)}")
    # A SCHEDULED CHECK DOES NOT GATE THE TIMER: the rows are the report, and a non-zero exit here would only
    # make systemd mark the unit failed while the record it exists to write is already on disk.
    return 0


if __name__ == "__main__":
    sys.exit(main())
