'''Oracle for t1202: the hourly sweep records what the box looked like, not just its verdict.

Why (workspace/rca/check-60f17848c499.md): the 14.1 GB and 15.6 GB skip lines prove the disk
sampler exists only to SKIP a sweep. Nothing records load average, nproc, the --jobs value, the
suite count, or the free GB alongside a sweep's verdict, so every 'the box was under load'
explanation is a hypothesis with no row behind it.

This suite pins the fix as behaviour, not shape: a row per phase, appended as it happens, whose
free_gb is MEASURED on the filesystem the sweep writes into (a constant fails), whose knobs carry
the sweep's own DISTINCT values (one placeholder filling every field fails), and which refuses an
invented number where the value is not yet known (the start row has no suite count yet).

Run: python3 scripts/tests/test_hourly_sweep_resources.py   (pytest is not installed; the runner
at the bottom is the runner.)
'''
import inspect
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'projects' / 'app-repo')]

import hourly_checks

KEYS = ('at', 'phase', 'free_gb', 'load1', 'load5', 'load15', 'nproc', 'jobs', 'suites')


def _rows(path):
    text = Path(path).read_text(encoding='utf-8')
    assert text.endswith('\n'), f'a ledger record is one line ending in a newline, got {text!r}'
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def test_sweep_resources_measures_the_filesystem_it_is_given():
    with tempfile.TemporaryDirectory() as td:
        r = hourly_checks.sweep_resources(td, jobs=2, suites=7)
        assert isinstance(r, dict), f'expected a dict, got {type(r).__name__}'
        missing = [k for k in KEYS if k not in r]
        assert not missing, f'sweep_resources row is missing keys {missing}: {r!r}'
        want = shutil.disk_usage(td).free / (1024 ** 3)
        got = float(r['free_gb'])
        assert abs(got - want) < 0.5, (
            f'free_gb {got} is not the free space of the filesystem under {td} ({want:.3f} GB). '
            'A constant, a cached value, or a read of the repository filesystem is not a '
            'measurement -- the sweep fills TMPDIR, and TMPDIR is the disk that must be sampled.')
        assert got > 0.0, f'free_gb must be a positive number of GB, got {r["free_gb"]!r}'
        assert r['jobs'] == 2, f'jobs must be the sweep\'s --jobs value, got {r["jobs"]!r}'
        assert r['suites'] == 7, f'suites must be the value handed in, got {r["suites"]!r}'
        for k in ('load1', 'load5', 'load15'):
            v = float(r[k])
            assert v >= 0.0, f'{k} must be a non-negative load average, got {r[k]!r}'
        assert int(r['nproc']) >= 1, f'nproc must be at least 1, got {r["nproc"]!r}'


def test_record_appends_one_json_line_per_phase_and_returns_the_row():
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / 'hourly-checks.jsonl'
        a = hourly_checks.record_sweep_resources('start', ledger, tmpdir=td, jobs=2, suites=None)
        b = hourly_checks.record_sweep_resources('end', ledger, tmpdir=td, jobs=2, suites=41)
        assert ledger.is_file(), f'no resource row was written to {ledger}'
        rows = _rows(ledger)
        assert len(rows) == 2, f'one row per phase, appended in call order; got {len(rows)}: {rows!r}'
        assert [r['phase'] for r in rows] == ['start', 'end'], (
            f"phase must be the phase asked for, in order, got {[r['phase'] for r in rows]!r}")
        for row, ret in zip(rows, (a, b)):
            assert ret == row, (
                f'the returned row must be the row written, so a caller sees what landed: '
                f'{ret!r} != {row!r}')
            missing = [k for k in KEYS if k not in row]
            assert not missing, f'row is missing keys {missing}: {row!r}'
            at = row['at']
            assert isinstance(at, str) and len(at) == 20 and at[4] == '-' and at[-1] == 'Z', (
                f'at must be a UTC timestamp like 2026-10-14T09:37:33Z, got {at!r}')
            assert isinstance(row['free_gb'], (int, float)) and not isinstance(row['free_gb'], bool), (
                f'free_gb must be a number, got {row["free_gb"]!r}')
        assert rows[0]['suites'] is None, (
            f'a row written before the sweep runs has no suite count to report; inventing one '
            f'fails: {rows[0]["suites"]!r}')
        assert rows[1]['suites'] == 41, f'the end row carries the suite count, got {rows[1]["suites"]!r}'


def test_the_sweeps_own_defaults_point_at_the_sweep():
    sig = inspect.signature(hourly_checks.record_sweep_resources)
    assert sig.parameters['ledger'].default == hourly_checks.LEDGER, (
        f'record_sweep_resources must default to the ledger the RCA reads, got '
        f'{sig.parameters["ledger"].default!r}')
    assert sig.parameters['tmpdir'].default in (None, str(hourly_checks.ENV['TMPDIR'])) , (
        'the tmpdir default is either explicit (the sweep passes ENV["TMPDIR"]) or absent; a '
        'hard-coded system temp path samples the wrong filesystem')


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
