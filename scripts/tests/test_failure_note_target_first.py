#!/usr/bin/env python3
"""B5: a failure note LEADS with the TARGET suite's own result and labels every other failing suite COLLATERAL,
with its count. Fake task store; the log is t1432's real attempt trail, cut to its markers.

2026-10-30, REWORK-INTERVIEWS-2026-10-29 finding 1: t1432's note was 60 lines of two collateral suites while its
target had PASSED on that attempt, and Swift called a collateral suite "the target suite".

Run: python3 scripts/tests/test_failure_note_target_first.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import supervise as S  # noqa: E402

TARGET = "scripts/tests/test_coder_output_budget_clock.py"
FAIL = {"chunk": "p744-t1432-coder", "model": "swift", "at": "2026-10-21T07:48:50Z", "exit": 1,
        "brief": "brief-p744-t1432-coder.txt"}

COLLATERAL_ONLY = """\
  attempt 3 … tests fail (2: test_coder_first_budget.py, test_coder_slice_room.py, 228s)
--- test_coder_first_budget.py output (whole) ---
  [BAD]  Swift 262k window -> all the room: expected 250144, got 20400
4 passed, 4 failed
--- end test_coder_first_budget.py ---
--- test_coder_slice_room.py output (whole) ---
FAIL: first_budget hands the reply every token the window has beside the prompt
--- end test_coder_slice_room.py ---
  attempt 4 … asked for 1 file(s), gave 0 new (2767 tok, 145s)
  attempt 5 … tests fail (2: test_coder_first_budget.py, test_coder_slice_room.py, 861s)
--- test_coder_first_budget.py output (whole) ---
4 passed, 4 failed
--- end test_coder_first_budget.py ---
--- test_coder_slice_room.py output (whole) ---
FAIL: first_budget hands the reply every token the window has beside the prompt
--- end test_coder_slice_room.py ---
  stopping: two identical TESTS-FAILED failures
"""

TARGET_RED = """\
  attempt 2 … tests fail (2: test_coder_output_budget_clock.py, test_coder_slice_room.py, 300s)
--- test_coder_output_budget_clock.py output (whole) ---
  [BAD]  the cap exists and sits above the floor
3 passed, 1 failed
--- end test_coder_output_budget_clock.py ---
--- test_coder_slice_room.py output (whole) ---
FAIL: slice room
--- end test_coder_slice_room.py ---
"""

FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {name}" + ("" if ok else f"\n         got: {detail}"))


class Store:
    def __init__(self):
        self.notes = []

    def add_note(self, tid, text, who=None):
        self.notes.append((tid, text))

    def set_meta(self, tid, **kw):
        pass


def note_for(log, target=TARGET):
    st = Store()
    S.escalate_failure(FAIL, log, store=st, chart=lambda role: "manager", target=target)
    return st.notes[-1][1] if st.notes else ""


n = note_for(COLLATERAL_ONLY)
first = n.splitlines()[0] if n else ""
check("the note's FIRST line is the target's own result: it passed on the last attempt",
      first == "TARGET SUITE test_coder_output_budget_clock.py: PASSED on attempt 5.", first)
check("the other failing suites are named COLLATERAL, with their count",
      "COLLATERAL: 2 other suite(s) failed: test_coder_first_budget.py, test_coder_slice_room.py" in n, n[:400])
check("each collateral section in the log tail is labelled",
      "--- COLLATERAL test_coder_slice_room.py output (whole) ---" in n
      and "--- test_coder_slice_room.py output (whole) ---" not in n, n[-600:])

n = note_for(TARGET_RED)
first = n.splitlines()[0] if n else ""
check("a target still red leads with STILL FAILING and its own count",
      first == "TARGET SUITE test_coder_output_budget_clock.py: STILL FAILING on attempt 2 -- 3 passed, 1 failed.",
      first)
check("  and only the OTHER suite is collateral",
      "COLLATERAL: 1 other suite(s) failed: test_coder_slice_room.py." in n, n[:400])
check("  and the target's own section is NOT relabelled",
      "--- test_coder_output_budget_clock.py output (whole) ---" in n, n[-600:])

n = note_for("  attempt 1 … does not compile (900 tok, 40s)\n")
check("an attempt that never reached the suites says the target was NOT RUN",
      n.splitlines()[0].startswith("TARGET SUITE test_coder_output_budget_clock.py: NOT RUN on the last attempt (1)"),
      n.splitlines()[0] if n else "")

n = note_for(COLLATERAL_ONLY, target=None)
check("no target recorded: the note says so rather than promoting a suite to target",
      n.splitlines()[0].startswith("TARGET SUITE: none recorded"), n.splitlines()[0] if n else "")

print(f"test_failure_note_target_first: {'ok' if not FAILED else f'{FAILED} failed'}")
sys.exit(1 if FAILED else 0)
