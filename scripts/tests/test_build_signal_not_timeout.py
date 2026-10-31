#!/usr/bin/env python3
"""A build the memory guard stopped is a yield, not a timed-out attempt (2026-10-24).

WHY. Builds run under `timeout 3600`. The guard's build tier matches any argv containing coder.py --
the `timeout` wrapper included -- and a SIGTERM to `timeout` makes it exit 124, timeout(1)'s own
"timed out" code. run_job then printed "BUILD WALL CLOCK: stopped after 3600 s" for runs of 92, 212
and 834 s (p665-t1247, p685-t1312, p677-t1266 on 2026-10-24, each at the second memory-guard.jsonl
records a builds shed), recorded exit 124, and futile() (FUTILE_AFTER = 1) parked each chunk on
swift-junior as "came to nothing" -- before the model had run: all three were still baselining.

THE RULE: exit 124 well short of BUILD_WALL_S is a signal, not the wall clock; it is a yield.
Pure: fixtures only.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "projects" / "local-inference")]
import dispatch_loop as D  # noqa: E402
import loop_breaker as LB  # noqa: E402

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


have = hasattr(D, "build_exit")
check("dispatch_loop.build_exit exists", have, True)
if have:
    W = D.BUILD_WALL_S
    check("124 after 92 s is a signal, recorded as a SIGTERM yield", D.build_exit(124, 92.0), 143)
    check("124 after 834 s likewise", D.build_exit(124, 834.0), 143)
    check("124 at the wall clock is a real timeout", D.build_exit(124, W + 1.0), 124)
    check("124 with no measured time is left as it was", D.build_exit(124, None), 124)
    check("other exits pass through", (D.build_exit(1, 50.0), D.build_exit(0, 50.0), D.build_exit(2, 5.0)), (1, 0, 2))
    check("143 is a yield", 143 in D.YIELD_EXITS, True)


def fin(exit_, secs, at="2026-10-24T13:18:47Z", chunk="p665-t1247-run-tests"):
    return {"event": "finished", "kind": "production", "chunk": chunk, "brief_sha256": "s1",
            "exit": exit_, "seconds": secs, "at": at, "model": "swift-junior:latest"}


# The ledger already holds 124 rows from guard kills: futile() must read them by their seconds too,
# or the six chunks parked this way stay parked.
D._harness_bug_keys = lambda: set()
D.harness_fixed_at = lambda: None
bad, why = D.futile("p665-t1247-run-tests", "s1", [fin(124, 92)])
check("a recorded 124 after 92 s does not park the chunk", bad, False)
bad, why = D.futile("p665-t1247-run-tests", "s1", [fin(124, D.BUILD_WALL_S + 5)])
check("a recorded 124 at the wall clock still does", bad, True)

# The loop breaker reads the same rows.
rows = [fin(124, 92, f"2026-10-24T0{i}:00:00Z") for i in range(1, 4)]
check("three guard kills are not a loop", LB.chunk_loop("p665-t1247-run-tests", "s1", rows)[0], False)

print(f"test_build_signal_not_timeout: {'FAILED' if fails else 'ok'} ({fails} failure(s))")
sys.exit(1 if fails else 0)
