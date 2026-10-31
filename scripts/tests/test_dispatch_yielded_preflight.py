#!/usr/bin/env python3
"""t387: a preflight that yielded (exit 4, lease held) is not counted as a verdictless review.

Against dispatch_loop.classify() with fixture history; never the live ledgers.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import dispatch_loop as DL  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def held_reason(exits):
    brief = "brief-p48-task-sessions.txt"
    sha = DL.brief_sha(brief)
    chunk = {"chunk": "p48-task-sessions", "brief": brief, "files": "scripts/task_sessions.py",
             "target": "scripts/tests/test_task_sessions.py"}
    # A CRASHED RUN HAS A PROCESS (2026-10-28 RCA): a row with pid 0, no exit and no duration is a
    # REFUSED duplicate launch, which never ran and must not count. These rows are meant to be runs
    # that finished, so they carry a pid and a duration; the yield case still sorts itself out on exit 4.
    hist = [{"event": "finished", "kind": "preflight", "brief_sha256": sha, "exit": e,
             "pid": 4242, "seconds": 30} for e in exits]
    # The brief is real but its TASK's status is live state: t386 closed on 2026-10-01 and classify()
    # then skips the chunk as finished before this rule is reached. Pin it open for the fixture.
    DL.task_closed = lambda brief_name: None
    out = DL.classify([chunk], [], lambda target: (True, "red"), history=hist,
                      objections=[])
    skipped = out[2]
    return next((why for c, why in skipped if c == "p48-task-sessions"
                 and "without recording a verdict" in why), None)


def main():
    report("two YIELDED preflights (exit 4) do not hold the brief", held_reason([4, 4]), None)
    report("two CRASHED preflights (exit 1) still hold it",
           held_reason([1, 1]) is not None, True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
