#!/usr/bin/env python3
"""t387 oracle: a HELD reviewer lease is a WAIT (exit 4), not a failed review.

Overnight 2026-09-23/24 every preflight on the manager model died with an unhandled
modellease.Held (ask_q16 held the lease for hours), dispatch_loop counted each crash as a review
that "ended without recording a verdict", and after two it held five briefs for Claude.

Drafted by Q4 (manager, t387 job 20); rewritten by Claude to run IN-PROCESS against a fake lease:
Q4's draft planted a real lease in the LIVE workspace/scoping-lease.json for up to 2 minutes,
which would stall the real manager -- and crash the test whenever a real job held it.
Q4's three assertions are kept.
"""
import contextlib
import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import modellease  # noqa: E402
import preflight_brief as pb  # noqa: E402

HELD_EXIT = 4          # dispatch_loop.YIELD_EXITS already reads 4 as "yield, retry next tick"
PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        pb.LEASE = Path(tmp) / "lease.json"
        pb.OBJECTIONS = Path(tmp) / "objections.jsonl"

        def held(*a, **k):
            raise modellease.Held("model 'qwen3.8:27b-mtp-q4_K_M' is held by '999-x' for "
                                  "'claude-driven manager: 07-hub-status-snapshot' until 0 (expires)")
        pb.modellease.acquire = held
        sys.argv = ["preflight_brief.py", "--brief", str(ROOT / "workspace" / "briefs" /
                                                         "brief-p48-task-sessions.txt"),
                    "--files", "scripts/task_sessions.py",
                    "--target", "scripts/tests/test_task_sessions.py"]
        err, out, code, crashed = io.StringIO(), io.StringIO(), None, None
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
            try:
                pb.main()
                code = 0
            except SystemExit as e:
                code = e.code
            except modellease.Held as e:
                crashed = f"unhandled Held: {e}"
        report("a held lease is not an unhandled exception", crashed, None)
        report("it exits with the wait code (4)", code, HELD_EXIT)
        report("it says HELD in a line the scheduler can read", "HELD" in err.getvalue() + out.getvalue(),
               True)
        report("it records NO objection row (a wait is not a verdict)",
               pb.OBJECTIONS.exists() and pb.OBJECTIONS.read_text().strip() != "", False)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
