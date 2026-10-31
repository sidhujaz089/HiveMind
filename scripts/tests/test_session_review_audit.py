#!/usr/bin/env python3
"""session_review_audit.due(): when the routine audit of the review script runs.

Hermetic: no gold files read, no task filed, nothing written. The audit must run when it has no
score, when the watched script changed, and once a day -- and NOT on every 10-minute tick, or the
dispatcher pays ~20 s each time for a number that cannot have moved.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import session_review_audit as A  # noqa: E402

FAILED = 0


def check(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}] {label}: {got!r}")


now = 1_000_000_000
last = {"code": "abc", "epoch": now - 600}
check("no score yet -> due", A.due(now, last={}, h="abc")[0], True)
check("same code, 10 min old -> not due", A.due(now, last=last, h="abc")[0], False)
check("script changed -> due", A.due(now, last=last, h="xyz")[0], True)
check("a day old -> due", A.due(now + A.DAY, last=last, h="abc")[0], True)
check("the weekly task filing is gone -- script-audit.timer replaces it (t790 step 5)",
      hasattr(A, "WEEKLY_TITLE") or "(weekly)" in open(A.__file__, encoding="utf-8").read(), False)
print(f"{'FAILED' if FAILED else 'ok'}: {FAILED} failure(s)")
sys.exit(1 if FAILED else 0)
