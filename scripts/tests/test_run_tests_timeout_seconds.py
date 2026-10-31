'''Oracle for t1202: a timed-out suite is recorded with the seconds it ACTUALLY took.

Why (workspace/rca/check-60f17848c499.md): scripts/run_tests.py's _once() returns the CAP as the
elapsed time on the timeout path, so workspace/suite-state.jsonl records a 301-second suite and a
599-second suite as the same '300.0', and no ledger query can say what the timeout class costs.
The fix separates cost from budget: seconds is real elapsed time, cap is the budget, and a row
for a suite that did not time out gains no new key at all.

The probe suite is written into a temp dir and named so run_tests.TIMEOUT_FOR (a dict read at call
time inside _once) can give it a small budget: no live server, no model, no fixed /tmp path, and
the child is killed by _once's own process-group teardown.

Run: python3 scripts/tests/test_run_tests_timeout_seconds.py   (pytest is not installed.)
'''
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'projects' / 'app-repo')]

import run_tests

PROBE = 'test_zz_t1202_timeout_probe.py'
SLEEP = 30
CAP = 2


def _install_probe(td):
    suite = Path(td) / PROBE
    suite.write_text('import time\ntime.sleep(%d)\n' % SLEEP, encoding='utf-8')
    saved = dict(run_tests.TIMEOUT_FOR)
    run_tests.TIMEOUT_FOR[PROBE] = CAP
    return suite, saved


def _restore(saved):
    run_tests.TIMEOUT_FOR.clear()
    run_tests.TIMEOUT_FOR.update(saved)


def test_timeout_returns_real_elapsed_seconds_not_the_cap():
    with tempfile.TemporaryDirectory() as td:
        suite, saved = _install_probe(td)
        try:
            t0 = time.time()
            res = run_tests._once(suite)
            parent = time.time() - t0
        finally:
            _restore(saved)
        assert len(res) >= 3, f'_once must still answer a 3-tuple at minimum, got {len(res)} values'
        rc, secs, out = res[0], res[1], res[2]
        assert rc == -9, f'a suite killed at its cap must report -9, got {rc!r}'
        assert 'TIMED OUT' in out, f'the timeout detail must name the budget it hit, got {out!r}'
        assert isinstance(secs, (int, float)) and not isinstance(secs, bool), (
            f'seconds must be a number, got {secs!r}')
        assert secs != CAP, (
            f'seconds on the timeout path is the cap ({CAP}): a 301-second suite and a '
            f'599-second suite are recorded as the same number, which is the defect. Report the '
            f'elapsed time ({CAP + 1}..{SLEEP} range expected), not the budget.')
        assert 0.0 < secs < SLEEP, f'seconds {secs} is outside the plausible (0, {SLEEP}) range'
        assert abs(secs - parent) < 1.0, (
            f'seconds {secs} is not the interval the parent measured ({parent:.2f}s): a constant, '
            'a clock read before the run, or a copied cap fails here.')
        assert len(res) == 4 and res[3] == CAP, (
            f'the budget must travel with the row as its own value, got {len(res)} values: {res!r}')


def test_record_suite_state_adds_cap_without_disturbing_seconds():
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / 'suite-state.jsonl'
        saved_state = run_tests.SUITE_STATE
        run_tests.SUITE_STATE = state
        try:
            plain = run_tests.record_suite_state(PROBE, -9, 4.7, owner='t1202')
            capped = run_tests.record_suite_state(PROBE, -9, 4.7, owner='t1202', cap=CAP)
        finally:
            run_tests.SUITE_STATE = saved_state
        assert state.is_file(), f'no row written to {state}'
        rows = [json.loads(ln) for ln in state.read_text(encoding='utf-8').splitlines() if ln.strip()]
        assert len(rows) == 2, f'each call appends exactly one row, got {len(rows)}: {rows!r}'
        first, second = rows
        assert 'cap' not in first, (
            f'a suite that did not time out must record exactly the keys it records today, '
            f'got {sorted(first)}')
        assert second.get('cap') == CAP, f'a timed-out suite must record the cap, got {second.get("cap")!r}'
        for row in rows:
            assert row['seconds'] == 4.7, f'seconds must stay the elapsed time, got {row["seconds"]!r}'
            assert row['suite'] == PROBE, f'suite unchanged, got {row["suite"]!r}'
            assert row['owner'] == 't1202' and row['task_ref'] == 't1202', (
                f'owner and task_ref unchanged, got {row["owner"]!r} / {row["task_ref"]!r}')
            assert row['exit_code'] == -9, f'exit_code unchanged, got {row["exit_code"]!r}'
            assert row['unowned'] is False, (
                f'a RED row with a named owner is not unowned, got {row["unowned"]!r}')
        assert 'cap' not in plain, f'returned row must match what was written: {plain!r}'
        assert capped.get('cap') == CAP and capped['seconds'] == 4.7, (
            f'seconds and cap are distinct fields, not one value written twice: {capped!r}')


def test_run_one_records_a_timed_out_suite_with_real_seconds_and_the_cap():
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / 'suite-state.jsonl'
        suite, saved = _install_probe(td)
        saved_state = run_tests.SUITE_STATE
        run_tests.SUITE_STATE = state
        try:
            res = run_tests.run_one(suite)
        finally:
            _restore(saved)
            run_tests.SUITE_STATE = saved_state
        name, rc = res[0], res[1]
        assert name == PROBE, f'run_one answers for the suite it ran, got {name!r}'
        assert rc != 0, f'a suite killed at its cap is not a pass, got rc {rc!r}'
        assert state.is_file(), f'the verdict of a timed-out suite was not recorded to {state}'
        rows = [json.loads(ln) for ln in state.read_text(encoding='utf-8').splitlines() if ln.strip()]
        assert len(rows) >= 1, f'at least one ledger row is expected, got {len(rows)}'
        for row in rows:
            assert row.get('cap') == CAP, (
                f'a timed-out suite must carry the budget it was given, got {row.get("cap")!r} '
                f'in {row!r}')
            assert row['seconds'] != CAP, (
                f'seconds is the cap ({CAP}) again: the ledger cannot tell 301s from 599s. '
                f'Got {row!r}')
            assert row['seconds'] > CAP, f'seconds {row["seconds"]} must exceed the {CAP}s budget'
            assert row['exit_code'] != 0, f'a timed-out suite is recorded non-zero, got {row!r}'


def main():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    assert tests, 'no test functions found in this oracle'
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'  PASS {name}')
        except AssertionError as e:
            failed += 1
            print(f'  FAIL {name}: {e}')
        except Exception as e:
            failed += 1
            print(f'  FAIL {name}: {type(e).__name__}: {e}')
    print(f'{len(tests) - failed}/{len(tests)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
