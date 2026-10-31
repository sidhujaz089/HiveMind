#!/usr/bin/env python3
"""The dispatcher refuses a job started STORM_LIMIT times within STORM_WINDOW_S (2026-10-19: four
script reviews relaunched 318 times in 15 minutes; the memory guard killed Swift). And script reviews
count when the role that owns script_review wrote them (truncation_review.reviewer_models).

Run: python3 scripts/tests/test_launch_storm_guard.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dispatch_loop as DL  # noqa: E402
import truncation_review as T  # noqa: E402

bad = 0


def check(label, got, want):
    global bad
    bad += got != want
    print(f"  {'ok ' if got == want else 'BAD'} {label}: {got!r}")


now = time.time()
stamp = lambda s: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - s))  # noqa: E731
ident = ("script-review", "scripts/sweep.py", "abc")
rows = [{"event": "started", "kind": "script-review", "key": "scripts/sweep.py", "brief_sha256": "abc",
         "at": stamp(60 * i)} for i in range(7)]
rows += [{"event": "started", "kind": "script-review", "key": "scripts/sweep.py", "brief_sha256": "abc",
          "at": stamp(3 * 3600)}]
check("starts inside the window are counted, older ones not", DL.recent_starts(rows, ident, now), 7)
check("seven is past the limit", DL.recent_starts(rows, ident, now) >= DL.STORM_LIMIT, True)
check("another job is not counted", DL.recent_starts(rows, ("script-review", "scripts/x.py", "abc"), now), 0)
src = Path(DL.__file__).read_text(encoding="utf-8")
check("launch() refuses a storm", "storm = recent_starts(history, ident)" in src, True)
check("a review by the script_review owner's model counts",
      T._counts({"model": "swift"}, {"swift", "qwen-q4"}), True)
check("a review by some other model does not", T._counts({"model": "gemma"}, {"swift"}), False)
print(f"{'FAILED' if bad else 'ok'}")
sys.exit(1 if bad else 0)
