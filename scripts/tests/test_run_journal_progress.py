#!/usr/bin/env python3
"""t1153 -- the sweep journal's per-suite rows must carry the suite INDEX.

WHY (RCA workspace/rca/ca5e3987fdfa.md): answering Jas's "That's been going for 3 hours. Is
it stuck?" meant stat-ing a log whose mtime was 9 minutes stale (transcript 1721-1731).
The fix asked for is a per-suite line a person can tail: suite index, elapsed, last write ts.

VERIFIED in scripts/run_tests.py as it stands: _tick (line 161) already writes elapsed
(secs) and last-write ts (at) into the kind="suite" row (lines 168-171), and the kind="start"
row carries the denominator (line 554). What the suite row does NOT carry is its INDEX: _tick
computes it (global _DONE; _DONE += 1; n = _DONE, lines 162-164) and prints it to stderr only
(line 166: "  [{n:3}/{_TOTAL}] ..."), so tail cannot say how far through the sweep a run was.

No live state is written: JOURNAL and SUITE_STATE are pointed at a temp dir, and the last
test asserts the live workspace/sweep-journal.jsonl is byte-identical after a run.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import run_tests  # noqa: E402


def _point_at_temp(tmp, total):
    """Redirect the journal and the suite ledger into tmp; reset the sweep counters."""
    run_tests.JOURNAL = tmp / "journal.jsonl"
    run_tests.SUITE_STATE = str(tmp / "suite-state.jsonl")
    run_tests._TREE = "oracle-tree"
    run_tests._TOTAL = total
    run_tests._DONE = 0


def _suite_rows():
    return [r for r in run_tests._rows() if r.get("kind") == "suite"]


def test_suite_row_carries_its_index_and_the_sweep_denominator():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _point_at_temp(tmp, 2)
        run_tests._tick("test_alpha.py", 0, 1.5)
        run_tests._tick("test_beta.py", 1, 0.25)
        rows = _suite_rows()
        assert len(rows) == 2, f"expected 2 suite rows, got {rows}"
        # 1-BASED AND DISTINCT: a constant index satisfies "a number is there" and tells a
        # reader nothing about progress, so both positions are pinned by value.
        assert rows[0].get("index") == 1, f"first suite row: {rows[0]}"
        assert rows[1].get("index") == 2, f"second suite row: {rows[0]}"
        # the denominator, so 'tail -3' answers 'N of how many' without the start row
        assert rows[0].get("total") == 2, rows[0]
        assert rows[1].get("total") == 2, rows[1]
        # elapsed is the SECONDS THIS SUITE TOOK, per suite -- not a shared placeholder
        assert rows[0]["secs"] == 1.5, rows[0]
        assert rows[1]["secs"] == 0.25, rows[1]
        # the fields that answer the question today must survive the change
        assert rows[0]["suite"] == "test_alpha.py" and rows[0]["rc"] == 0, rows[0]
        assert rows[1]["suite"] == "test_beta.py" and rows[1]["rc"] == 1, rows[1]
        assert isinstance(rows[0].get("at"), str) and rows[0]["at"].endswith("Z"), rows[0]
        assert rows[0].get("tree") == "oracle-tree", rows[0]


def test_a_resumed_sweep_continues_the_numbering_it_reused():
    """main() sets _DONE = len(reused) before the first suite runs (run_tests.py ~line 566).
    A resumed sweep that numbers from 1 again shows the reader '1/267' twice in one sweep,
    so the index must come from _DONE, not from the position in the filtered list."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _point_at_temp(tmp, 267)
        run_tests._DONE = 247          # 247 suites already passed on this tree
        run_tests._tick("test_zulu.py", 0, 750.7)   # the coder-suite time the RCA quoted
        rows = _suite_rows()
        assert len(rows) == 1, rows
        assert rows[0].get("index") == 248, f"expected 248 (247 reused + 1), got {rows[0]}"
        assert rows[0].get("total") == 267, rows[0]
        assert rows[0]["secs"] == 750.7, rows[0]


def test_running_this_oracle_leaves_the_live_journal_byte_identical():
    """Negative assertion: the oracle writes to its temp dir only. A fixture that appends to
    workspace/sweep-journal.jsonl would be measured by every later sweep."""
    live = ROOT / "workspace" / "sweep-journal.jsonl"
    before = live.read_bytes() if live.exists() else b""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _point_at_temp(tmp, 1)
        run_tests._tick("test_gamma.py", 0, 0.1)
        assert len(_suite_rows()) == 1
    after = live.read_bytes() if live.exists() else b""
    assert after == before, "the oracle wrote to the live workspace/sweep-journal.jsonl"


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except Exception as exc:            # noqa: BLE001
                failed += 1
                print(f"  FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"{len([1 for n, f in globals().items() if n.startswith('test_') and callable(f)]) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
