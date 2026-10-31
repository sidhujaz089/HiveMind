#!/usr/bin/env python3
"""t1184 -- a ledger that SHRINKS is a finding.

Evidence read this pass (Manager, 2026-10-15):
  scripts/reviewq.py:60   VERDICTS = REPO / "workspace" / "review-verdicts.jsonl"
  scripts/reviewq.py:413  with VERDICTS.open("a", encoding="utf-8") as f:
  scripts/reviewq.py:562  with VERDICTS.open("a", encoding="utf-8") as f:
So the only writer of the verdicts ledger already opens it in append mode: the
write path is NOT the defect t1184 describes.  What it is: an outside process
rewrote the file with older content (the task's second note: four tracked files
rewritten with their PRE-commit content at 14:48:10, mtimes .43, no stash and no
checkout run).  And it has happened twice: `wc -l workspace/review-verdicts.jsonl`
and `git show HEAD:workspace/review-verdicts.jsonl | wc -l` both give 212 today,
while the task note says the ledger had been re-recorded to 225 -- 13 rows are
gone again, and nothing in the tree notices, because no check compares a ledger's
row count to the highest count it has ever had.  (A check that compared to HEAD
could not catch the 2026-10-14 loss: those rows were never committed, so HEAD and
the live file agreed at 212.)

Red on main for exactly that reason: scripts/check_workspace.py defines no
ledger_highwater_check, no MONITORED_LEDGERS, no LEDGER_HIGHWATER.  Every test
below starts by asserting the name exists, so a missing change reads as a named
failure, never a traceback.

Asserted as BEHAVIOUR, not shape:
  * the finding text names the ledger and BOTH counts, and says so in words
    (a message that just says 'shrank' gives a reader nothing to act on);
  * the mark ratchets UP only -- an implementation that stores 'the count it saw
    last run' passes tests 5-6 and fails test 7 (a ledger that fell 5->2 and
    recovered to 4 is still below 5 and must fire again);
  * the check is pointed at the real ledger (MONITORED_LEDGERS contains
    workspace/review-verdicts.jsonl, resolved) and has a call site in the module,
    so a function nobody calls cannot pass;
  * rows are DISTINCT values, so a check that counts bytes cannot pass.

No live state, no /tmp paths, no model call, no subprocess, no process left
running: every ledger and every state file this file touches lives inside a
tempfile.TemporaryDirectory().
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # repo root, from scripts/tests/
sys.path.insert(0, str(ROOT / "scripts"))

import check_workspace                             # noqa: E402

CHECK = getattr(check_workspace, "ledger_highwater_check", None)
MONITORED = getattr(check_workspace, "MONITORED_LEDGERS", None)
MARK = getattr(check_workspace, "LEDGER_HIGHWATER", None)

LEDGER_NAME = "review-verdicts.jsonl"
MISSING = " is not defined in scripts/check_workspace.py (the change this oracle is scoped on)"


def _rows(path, n):
    """Write n JSONL rows, each DISTINCT: one repeated placeholder string cannot
tell a lost row from a shortened one."""
    path.write_text("".join('{"n": %d, "v": "verdict-%d"}\n' % (i, i) for i in range(n)),
                    encoding="utf-8")


def _mark(state, key, value):
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({key: value}), encoding="utf-8")


def _read_mark(state, key):
    return json.loads(state.read_text(encoding="utf-8"))[key]


def test_names_exist():
    assert callable(CHECK), "ledger_highwater_check" + MISSING
    assert isinstance(MONITORED, (list, tuple)), "MONITORED_LEDGERS" + MISSING
    assert isinstance(MARK, Path), "LEDGER_HIGHWATER" + MISSING


def test_monitors_the_ledger_that_was_lost():
    assert callable(CHECK), "ledger_highwater_check" + MISSING
    names = {Path(p).name for p in MONITORED}
    assert LEDGER_NAME in names, (
        "MONITORED_LEDGERS must include workspace/%s -- that is the ledger whose rows "
        "vanished twice; watching any other file proves nothing (found: %s)"
        % (LEDGER_NAME, sorted(names)))
    live = (ROOT / "workspace" / LEDGER_NAME).resolve()
    resolved = {Path(p).resolve() for p in MONITORED}
    assert live in resolved, (
        "MONITORED_LEDGERS must name the file at workspace/%s, repo-relative resolved "
        "(%s), not a lookalike name: %s" % (LEDGER_NAME, live, sorted(str(p) for p in resolved)))


def test_mark_lives_in_the_repo_ledger_area():
    assert isinstance(MARK, Path), "LEDGER_HIGHWATER" + MISSING
    assert MARK.name == "ledger-highwater.json", (
        "LEDGER_HIGHWATER must be workspace/ledgers/ledger-highwater.json, got %s" % MARK)
    assert ROOT in MARK.resolve().parents, (
        "LEDGER_HIGHWATER must live inside the repo so session_end runs against it: %s" % MARK)


def test_first_run_establishes_the_baseline_quietly():
    assert callable(CHECK), "ledger_highwater_check" + MISSING
    with tempfile.TemporaryDirectory() as td:
        ledger, state = Path(td) / LEDGER_NAME, Path(td) / "highwater.json"
        _rows(ledger, 3)
        out = CHECK(ledger, state)
        assert isinstance(out, list) and out == [], (
            "first run has nothing to compare to and must return [], got %r" % (out,))
        assert state.exists(), "first run must record a baseline in the state file"
        assert _read_mark(state, str(ledger)) == 3, (
            "the baseline must be the 3 non-empty rows of the ledger, got %r"
            % _read_mark(state, str(ledger)))


def test_growth_is_quiet_and_raises_the_mark():
    assert callable(CHECK), "ledger_highwater_check" + MISSING
    with tempfile.TemporaryDirectory() as td:
        ledger, state = Path(td) / LEDGER_NAME, Path(td) / "highwater.json"
        _mark(state, str(ledger), 3)
        _rows(ledger, 5)
        out = CHECK(ledger, state)
        assert out == [], "a growing ledger must produce no finding, got %r" % (out,)
        assert _read_mark(state, str(ledger)) == 5, "the mark must rise to 5, so a later 4 fires"


def test_shrink_is_a_finding_naming_the_ledger_and_both_counts():
    assert callable(CHECK), "ledger_highwater_check" + MISSING
    with tempfile.TemporaryDirectory() as td:
        ledger, state = Path(td) / LEDGER_NAME, Path(td) / "highwater.json"
        _mark(state, str(ledger), 5)
        _rows(ledger, 2)
        out = CHECK(ledger, state)
        assert len(out) == 1, (
            "a shrink from 5 to 2 must produce exactly one finding, got %r" % (out,))
        f = str(out[0])
        assert LEDGER_NAME in f, (
            "the finding must name the ledger -- a reader with two counts and no file name "
            "cannot act on it: %s" % f)
        assert "5" in f and "2" in f, (
            "the finding must carry both counts (was 5, now 2): %s" % f)
        low = f.lower()
        assert "shrink" in low or "lost" in low, (
            "the finding must say what happened in words a person acts on: %s" % f)


def test_the_mark_never_goes_down():
    assert callable(CHECK), "ledger_highwater_check" + MISSING
    with tempfile.TemporaryDirectory() as td:
        ledger, state = Path(td) / LEDGER_NAME, Path(td) / "highwater.json"
        _mark(state, str(ledger), 5)
        _rows(ledger, 2)
        CHECK(ledger, state)
        assert _read_mark(state, str(ledger)) == 5, (
            "a shrink must not lower the mark: overwriting 5 with 2 means the ledger can come "
            "back at 3, 4, even 40 and stay under the radar forever")
        _rows(ledger, 4)
        out = CHECK(ledger, state)
        assert len(out) == 1, (
            "recovered-to-4 is still below the mark of 5 and must fire again -- an "
            "implementation that compares to the PREVIOUS run's count passes the shrink test "
            "and fails this one: %r" % (out,))
        assert _read_mark(state, str(ledger)) == 5, (
            "the mark stays 5 after a 4-row check: %r" % (out,))
        _rows(ledger, 5)
        out = CHECK(ledger, state)
        assert out == [], "back at the mark (5) must be quiet, got %r" % (out,)
        assert _read_mark(state, str(ledger)) == 5


def test_equal_count_is_quiet():
    assert callable(CHECK), "ledger_highwater_check" + MISSING
    with tempfile.TemporaryDirectory() as td:
        ledger, state = Path(td) / LEDGER_NAME, Path(td) / "highwater.json"
        _mark(state, str(ledger), 5)
        _rows(ledger, 5)
        out = CHECK(ledger, state)
        assert out == [], "an unchanged ledger must produce no finding: %r" % (out,)
        assert _read_mark(state, str(ledger)) == 5, (
            "an unchanged ledger must not touch the mark: %r" % (out,))


def test_blank_lines_are_not_rows():
    assert callable(CHECK), "ledger_highwater_check" + MISSING
    # A ledger appended to a file with no trailing newline gains a blank line. A
    # counter that counts raw newlines sees a fake row and can miss a lost one.
    with tempfile.TemporaryDirectory() as td:
        ledger, state = Path(td) / LEDGER_NAME, Path(td) / "highwater.json"
        _mark(state, str(ledger), 2)
        ledger.write_text('{"n": 1, "v": "kept"}\n\n   \n{"n": 2, "v": "sent-back"}\n\n',
                          encoding="utf-8")
        out = CHECK(ledger, state)
        assert out == [], (
            "2 JSON rows plus blanks must equal the mark of 2, not exceed it: %r" % (out,))
        assert _read_mark(state, str(ledger)) == 2, (
            "blank/whitespace-only lines must not count as rows: %r" % (out,))


def test_absent_ledger_does_not_raise_and_is_not_a_finding():
    assert callable(CHECK), "ledger_highwater_check" + MISSING
    # The file legitimately does not exist before its first row. A check that
    # raises here is disabled with --no-verify inside a week.
    with tempfile.TemporaryDirectory() as td:
        ledger, state = Path(td) / LEDGER_NAME, Path(td) / "highwater.json"
        out = CHECK(ledger, state)
        assert isinstance(out, list), (
            "an absent ledger must return a list, not raise: %r" % (out,))
        assert out == [], (
            "a ledger that has never been written is not a finding: %r" % (out,))


def test_the_check_has_a_call_site_in_the_module():
    assert callable(CHECK), "ledger_highwater_check" + MISSING
    import inspect
    src = inspect.getsource(check_workspace)
    n_def = src.count("def ledger_highwater_check")
    assert n_def == 1, (
        "expected exactly one definition of ledger_highwater_check, found %d" % n_def)
    assert src.count("ledger_highwater_check") - n_def >= 1, (
        "the module defines the check but never calls it: a green function nobody runs "
        "is the fixture that fails to fire")
    assert src.count("MONITORED_LEDGERS") >= 2, (
        "the call site must iterate MONITORED_LEDGERS, not the one file this oracle "
        "names, or the list is decoration")
    assert src.count("LEDGER_HIGHWATER") >= 2, (
        "the call site must use the state path constant, not a private path")


if __name__ == "__main__":
    _fns = [v for _k, v in sorted(globals().items())
            if _k.startswith("test_") and callable(v)]
    _failed = 0
    for _fn in _fns:
        try:
            _fn()
            print("PASS " + _fn.__name__)
        except Exception as _exc:
            _failed += 1
            print("FAIL " + _fn.__name__ + ": " + str(_exc))
    print("\n%d/%d passed" % (len(_fns) - _failed, len(_fns)))
    sys.exit(1 if _failed else 0)
