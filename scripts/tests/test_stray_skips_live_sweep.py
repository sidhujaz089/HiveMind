#!/usr/bin/env python3
"""Fail-first oracle for the sweep exemption in the stray-process rule (t2152).

Measured 2026-10-25 by the GX10 instance, as a warning in its handover: *merging run_tests.py while a
sweep is running makes it a "stray process" once it passes an hour, and the gate then reads 1 failed
until it ends.* The rule flags a process up ≥1h whose script changed after it started -- right for an
orphan, wrong for the sweep, which is bounded, recorded, and may legitimately run for hours.

`run_tests.py` writes its pid into workspace/run-logs/sweep.lock (t1888). That lock is the evidence
that the sweep is live work, so the holder is exempt. Every other long-lived repo process is unchanged.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import check_stale_services as css  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    check("the holder is readable from the lock", hasattr(css, "sweep_holder"), True)
    with tempfile.TemporaryDirectory() as td:
        lock = Path(td) / "sweep.lock"

        # 1. The live case: a lock naming a pid.
        lock.write_text(json.dumps({"pid": 4242, "by": "hourly check",
                                    "started": "2026-10-25T10:05:23Z"}), encoding="utf-8")
        check("a lock naming a pid yields that pid as a string", css.sweep_holder(lock), "4242")

        # 2. No lock: nothing is exempt (a stale lock is not a live sweep).
        lock.unlink()
        check("no lock means no exemption", css.sweep_holder(lock), None)

        # 3. An unreadable or malformed lock is not a pass in disguise: None, never a crash.
        lock.write_text("{not json", encoding="utf-8")
        check("an unreadable lock yields None, not an exception", css.sweep_holder(lock), None)
        lock.write_text("{}", encoding="utf-8")
        check("a lock naming no pid yields None", css.sweep_holder(lock), None)

        # 4. The rule really uses it, and only for the holder.
        src = (REPO / "scripts" / "check_stale_services.py").read_text(encoding="utf-8")
        body = src[src.index("def stray_repo_processes():"):]
        check("the stray rule compares each pid against the holder", "pid == sweep_pid" in body, True)
        check("  and it reads the holder from the lock file", "sweep_holder()" in body, True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
