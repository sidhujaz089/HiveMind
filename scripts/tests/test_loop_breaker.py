#!/usr/bin/env python3
"""One loop breaker for every stage (Jas, 2026-10-24: "How can we stop looping items?" -- "Build it").

THE RULE: the same work, at the same stage, with the same result LOOP_N times in a row and nothing
changed in between, stops and goes to the technical seat with the evidence. Each stage had its own cap;
the gaps looped: p668-t1250's preflight said NOT SATISFIABLE 67 times in 24 h (it took ~1 s, under the
5-second floor of the crash hold), and the manager queue scoped t785 46 times.

THE EXCEPTION: a failure many chunks share in the same window is the HARNESS, not this work
(dispatch_loop.futile, 2026-09-16: counting refusals once parked four innocent briefs; 2026-10-23 an
untracked suite refused every build). That is not a loop here.

Pure: every input is a fixture; nothing reads or writes the live ledgers.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


try:
    import loop_breaker as LB
except ImportError as e:
    print(f"  [BAD]  scripts/loop_breaker.py does not exist ({e})")
    print("test_loop_breaker: FAILED (1 failure(s))")
    sys.exit(1)


def fin(chunk, kind, exit_, at, sha="s1"):
    return {"event": "finished", "chunk": chunk, "kind": kind, "exit": exit_, "at": at, "brief_sha256": sha}


# --- p668-t1250: one chunk, preflight exit 2, three times, brief unchanged -> LOOP
h = [fin("p668-t1250-coder", "preflight", 2, f"2026-10-24T0{i}:00:00Z") for i in range(1, 4)]
loop, why = LB.chunk_loop("p668-t1250-coder", "s1", h)
check("three identical preflight results with the brief unchanged is a loop", loop, True)
check("  the reason starts LOOP: and names the stage and exit", (why.startswith("LOOP:"), "preflight" in why, "exit 2" in why), (True, True, True))

# --- two is not three
loop2, _ = LB.chunk_loop("p668-t1250-coder", "s1", h[:2])
check("two identical results is not yet a loop", loop2, False)

# --- an edited brief (new sha) starts over: the old rows are a different brief
loop3, _ = LB.chunk_loop("p668-t1250-coder", "s2", h)
check("a changed brief is not judged on the old brief's results", loop3, False)

# --- a success in between breaks the run
h4 = h[:2] + [fin("p668-t1250-coder", "preflight", 0, "2026-10-24T02:30:00Z")] + [fin("p668-t1250-coder", "preflight", 2, "2026-10-24T04:00:00Z")]
check("a success in between breaks the run", LB.chunk_loop("p668-t1250-coder", "s1", h4)[0], False)

# --- different results are not a loop (it is moving, even if failing)
h5 = [fin("p1-t1-x", "production", 1, "2026-10-24T01:00:00Z"), fin("p1-t1-x", "production", 124, "2026-10-24T02:00:00Z"),
      fin("p1-t1-x", "production", 1, "2026-10-24T03:00:00Z")]
check("different results in a row are not a loop", LB.chunk_loop("p1-t1-x", "s1", h5)[0], False)

# --- yields do not count and do not break the run
h6 = h[:2] + [fin("p668-t1250-coder", "preflight", 4, "2026-10-24T02:30:00Z")] + h[2:]
check("a yield neither counts nor breaks the run", LB.chunk_loop("p668-t1250-coder", "s1", h6)[0], True)

# --- harness-wide: the same failure on 3+ other chunks in the window is not this chunk's loop
others = [fin(f"p{n}-t{n}-y", "production", 2, f"2026-10-24T02:{n:02d}:00Z") for n in (10, 11, 12)]
hz = [fin("p9-t9-z", "production", 2, f"2026-10-24T0{i}:00:00Z") for i in range(1, 4)] + others
loopz, whyz = LB.chunk_loop("p9-t9-z", "s1", hz)
check("a failure shared by 3+ other chunks in the same window is the harness, not a loop", loopz, False)

# --- the live row shape: other chunks failing a DIFFERENT way do not excuse it
others2 = [fin(f"p{n}-t{n}-y", "production", 1, f"2026-10-24T02:{n:02d}:00Z") for n in (10, 11, 12)]
check("other chunks failing differently do not excuse a loop",
      LB.chunk_loop("p668-t1250-coder", "s1", h + others2)[0], True)

# --- a build that REFUSED to start (exit 2) is futile()'s call: usually the harness's sandbox
hr = [fin("p750-t1447-x", "production", 2, f"2026-10-24T0{i}:00:00Z") for i in range(1, 4)]
check("three build refusals (production exit 2) are left to futile(), not called a loop",
      LB.chunk_loop("p750-t1447-x", "s1", hr)[0], False)
ht = [fin("p753-t1455-x", "production", 124, f"2026-10-24T0{i}:00:00Z") for i in range(1, 4)]
check("three identical build timeouts are a loop", LB.chunk_loop("p753-t1455-x", "s1", ht)[0], True)

# --- scoping: the same step three times in a row
lines = ["t785: Manager pass (queue top): **BLOCKED** -- a",
         "t785: Manager pass (queue top): **BLOCKED** -- b",
         "x 10:00:00Z IDLE nothing",
         "t785: Manager pass (queue top): **BLOCKED** -- c",
         "t9: Manager pass (queue top): **BLOCKED** -- a",
         "t9: Manager pass (queue top): **SCOPED** -- b",
         "t9: Manager pass (queue top): **BLOCKED** -- c",
         "t7: Manager pass (queue top): **SCOPED** -- a",
         "t7: Manager pass (queue top): **SCOPED** -- b",
         "t7: Manager pass (queue top): **SCOPED** -- c"]
got = LB.scoping_loops(lines)
check("three identical scoping steps in a row is a loop", got.get("t785", {}).get("step"), "BLOCKED")
check("  it records how many passes it has seen", got.get("t785", {}).get("passes"), 3)
check("a step that changed in between is not", "t9" in got, False)
check("SCOPED three times is progress, not a loop", "t7" in got, False)

# --- scoping idempotence: a loop already acted on at N passes waits for N more
check("acted on at 3 passes: not again at 3", LB.due(got["t785"]["passes"], acted_at=3), False)
check("  nor at 5", LB.due(5, acted_at=3), False)
check("  but again at 6 (three more identical passes after a release)", LB.due(6, acted_at=3), True)
check("never acted on: due", LB.due(3, acted_at=None), True)

# --- only a task that can be scoped again is acted on
check("an open task in Production is acted on", LB.should_act({"status": "queued"}, True), True)
check("a task already with a person or role is not", LB.should_act({"status": "queued"}, False), False)
check("a done task is not", LB.should_act({"status": "done"}, True), False)
check("a cancelled task is not", LB.should_act({"status": "cancelled"}, True), False)
check("a missing task is not", LB.should_act({}, True), False)

print(f"test_loop_breaker: {'FAILED' if fails else 'ok'} ({fails} failure(s))")
sys.exit(1 if fails else 0)
