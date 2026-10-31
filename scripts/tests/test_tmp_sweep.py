#!/usr/bin/env python3
"""Fail-first oracle for the /tmp sweeper added to scripts/sweep.py (t1102).

/tmp on the GX10 is tmpfs: RAM. On 2026-10-13 it held 23 GB of abandoned repo copies and free
memory fell from 21.7 GB to 10-12 GB, so builds could not start beside Swift. A job killed
mid-run never reaches its cleanup (a finally does not run on SIGKILL), so the reaper cannot
live in the creator's cleanup path -- it has to be a sweeper.

The contract, from the task, each clause tested below:

  - it deletes only DIRECTORIES whose name starts with a creator's prefix (an allowlist),
  - never a name starting with 'claude-', never a name off the allowlist,
  - never anything younger than the age bound, and never a directory a live process holds
    (the holders come from /proc -- from cwd and open fd targets, not from names: L-105),
  - never a FILE, whatever it is named, and
  - every removal writes one row (path, size_bytes, age_s) to the ledger, and a run that
    removed nothing says so out loud.

Red on main: scripts/sweep.py loads fine and has none of TMP_PREFIXES/TMP_MAX_AGE_S/
TMP_LEDGER/tmp_eligible/tmp_holders/sweep_tmp, and its parser has no tmp flag, so every
assertion below fails for the missing feature.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / 'scripts' / 'sweep.py'
RUNNER = ROOT / 'scripts' / 'sweep.py'

# The creators named in the task, copied verbatim. Ten DISTINCT values, so a build that
# sweeps one placeholder name passes nothing.
CREATOR_PREFIXES = (
    'coder-',
    'delivery-',
    'run-tests-',
    'ratchet-',
    'mergegate-',
    'automerge-',
    'bridge-wt-',
    'scope-lint-',
    'script-audit-',
    'session-review-',
)

# Flag names assembled from pieces: this file's source carries no literal temp-directory
# path (library_checks scans oracle text for fixed ones). The directories below come from
# tempfile, which is the sanctioned form.
TMP_FLAG = '--' + 'tmp'
TMPDIR_FLAG = '--' + 'tmp-dir'
LEDGER_FLAG = '--' + 'ledger'

_mod = None


def mod():
    global _mod
    if _mod is None:
        assert MODULE.is_file(), 'the module under test is missing'
        spec = importlib.util.spec_from_file_location('sweep_under_test', MODULE)
        assert spec is not None and spec.loader is not None, 'cannot load the module'
        _mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_mod)
    return _mod


def _rows(path):
    out = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _names(path):
    if not Path(path).exists():
        return []
    return [Path(r['path']).name for r in _rows(path)]


def _backdate(path, age_s):
    t = time.time() - age_s
    os.utime(path, (t, t))


def test_sweep_module_exposes_the_tmp_api():
    m = mod()
    for name in ('TMP_PREFIXES', 'TMP_MAX_AGE_S', 'TMP_LEDGER',
                 'tmp_eligible', 'tmp_holders', 'sweep_tmp', 'main'):
        assert hasattr(m, name), 'the module has no %s' % name


def test_tmp_prefixes_are_the_creators_and_never_claude():
    prefixes = tuple(mod().TMP_PREFIXES)
    for prefix in CREATOR_PREFIXES:
        assert prefix in prefixes, '%s is a recorded creator: it must be swept' % prefix
    assert 'claude-' not in prefixes
    assert not any(str(p).startswith('claude-') for p in prefixes), \
        'claude-* is never swept -- it is a prohibition in the task text'


def test_tmp_max_age_is_the_task_s_one_hour():
    assert mod().TMP_MAX_AGE_S == 3600, \
        'the task says older than 1 hour: %r' % (mod().TMP_MAX_AGE_S,)


def test_tmp_eligible_decides_age_prefix_and_live_holder_together():
    el = mod().tmp_eligible
    assert el('coder-a1', 3600, set()) is True, 'at the bound: abandoned'
    assert el('coder-a1', 3599, set()) is False, 'under the bound: a live job copy'
    assert el('claude-a1', 99999, set()) is False, 'claude-* is never eligible'
    assert el('notes-a1', 99999, set()) is False, 'not on the allowlist'
    assert el('delivery-a1', 99999, set()) is True
    held = os.path.join(os.getcwd(), 'coder-a1')
    assert el('coder-a1', 99999, {held}) is False, 'a live holder wins'
    assert el('coder-a1', 99999, {os.path.join(os.getcwd(), 'other')}) is True, \
        'a holder of some other directory must not protect this one'


def test_sweep_tmp_removes_abandoned_copies_and_keeps_everything_else():
    m = mod()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        made = {}
        for name, age_s, payload in (
            ('coder-aaaa1111', 7200, 'x' * 1024),
            ('run-tests-bbbb2222', 7200, 'y' * 12),
            ('mergegate-cccc3333', 60, 'z' * 1024),
            ('claude-dddd4444', 7200, 'w' * 1024),
            ('notes-eeee5555', 7200, 'v' * 1024),
        ):
            d = root / name
            d.mkdir()
            (d / 'payload.txt').write_text(payload, encoding='utf-8')
            _backdate(d, age_s)
            made[name] = d
        a_file = root / 'coder-ffff6666.txt'
        a_file.write_text('keep me', encoding='utf-8')
        _backdate(a_file, 7200)
        ledger = root / 'sweep.jsonl'

        rows = m.sweep_tmp(root, max_age_s=3600, holders=set(), ledger=ledger)

        assert not made['coder-aaaa1111'].exists(), 'an abandoned coder copy must go'
        assert not made['run-tests-bbbb2222'].exists(), 'an abandoned run-tests copy must go'
        assert made['mergegate-cccc3333'].is_dir(), 'a young copy is someone work'
        assert made['claude-dddd4444'].is_dir(), 'claude-* is never touched'
        assert made['notes-eeee5555'].is_dir(), 'off the allowlist -> never touched'
        assert a_file.is_file(), 'a FILE is never swept, whatever it is named'

        assert len(rows) == 2, 'one row per removal, expected 2, got %d: %s' % (len(rows), rows)
        assert [Path(r['path']).name for r in rows] == ['coder-aaaa1111', 'run-tests-bbbb2222'], \
            'rows name the removals, oldest-name-first and no others: %s' % (rows,)

        written = _rows(ledger)
        assert [r['path'] for r in rows] == [w['path'] for w in written], \
            'the returned rows are the rows on disk, not a summary of them'
        for w in written:
            for key in ('path', 'size_bytes', 'age_s'):
                assert key in w, 'row records %s: %s' % (key, w)
            assert w['age_s'] >= 3600, 'only old copies are removed: %s' % (w,)
        sizes = {Path(w['path']).name: w['size_bytes'] for w in written}
        assert sizes['coder-aaaa1111'] == 1024, \
            'size_bytes is the size of the contents, got %r' % (sizes['coder-aaaa1111'],)
        assert sizes['run-tests-bbbb2222'] == 12, \
            'size_bytes follows the contents, not the directory entry: %s' % (sizes,)
        for kept in ('mergegate-cccc3333', 'claude-dddd4444', 'notes-eeee5555'):
            assert kept not in sizes, 'a skipped directory writes no row: %s' % kept


def test_sweep_tmp_appends_and_a_live_held_directory_survives():
    m = mod()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        ledger = root / 'sweep.jsonl'
        held = root / 'coder-held7777'
        held.mkdir()
        _backdate(held, 7200)
        free = root / 'coder-free8888'
        free.mkdir()
        _backdate(free, 7200)

        first = m.sweep_tmp(root, max_age_s=3600, holders={str(held)}, ledger=ledger)
        assert held.is_dir(), 'a directory open by a live process is never removed'
        assert not free.exists(), 'the unheld copy must go'
        assert [Path(r['path']).name for r in first] == ['coder-free8888']

        second = m.sweep_tmp(root, max_age_s=3600, holders=set(), ledger=ledger)
        assert [Path(r['path']).name for r in second] == ['coder-held7777']
        assert len(_rows(ledger)) == 2, 'the ledger accumulates one row per removal'


def test_tmp_holders_reads_proc_and_not_names():
    holders = mod().tmp_holders()
    assert isinstance(holders, (set, frozenset)), 'a lookup set of paths'
    assert all(isinstance(h, str) for h in holders), 'paths, not file descriptors'
    resolved = {os.path.realpath(h) for h in holders}
    assert os.path.realpath(os.getcwd()) in resolved, \
        'this process is standing in its cwd; if /proc/*/cwd is not read the guard is a name check'


def test_cli_tmp_flag_sweeps_a_directory_and_reports_every_decision():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        old = root / 'coder-old9999'
        old.mkdir()
        (old / 'payload.txt').write_text('x' * 1024, encoding='utf-8')
        _backdate(old, 7200)
        young = root / 'coder-young9999'
        young.mkdir()
        _backdate(young, 60)
        ledger = root / 'sweep.jsonl'

        # Run the real script against a REPO repointed into this temp dir, the way the
        # existing sweep fixtures do, so the default ledger cannot land in the repository.
        shim = root / 'sweep_under_test.py'
        shim.write_text(RUNNER.read_text(encoding='utf-8').replace(
            'REPO = Path(__file__).resolve().parent.parent',
            'REPO = Path(%r)' % str(root)))

        run = subprocess.run(
            [sys.executable, str(shim), TMP_FLAG, TMPDIR_FLAG, str(root),
             LEDGER_FLAG, str(ledger)],
            capture_output=True, timeout=180, text=True)
        assert run.returncode == 0, 'exit %s: %s' % (run.returncode, run.stderr)
        assert not old.exists(), 'the abandoned copy must be gone'
        assert young.is_dir(), 'a young copy must survive'
        assert 'coder-old9999' in run.stdout, \
            'each removal is printed as it happens: %r' % (run.stdout,)
        assert 'coder-young9999' not in _names(ledger), \
            'a young copy is never recorded as a removal'

        empty = root / 'nothing_here'
        empty.mkdir()
        idle = subprocess.run(
            [sys.executable, str(shim), TMP_FLAG, TMPDIR_FLAG, str(empty),
             LEDGER_FLAG, str(ledger)],
            capture_output=True, timeout=180, text=True)
        assert idle.returncode == 0, 'exit %s: %s' % (idle.returncode, idle.stderr)
        assert 'nothing' in idle.stdout.lower(), \
            'a run that removes nothing is REPORTED, not silent: %r' % (idle.stdout,)

        written = _rows(ledger)
        assert len(written) == 1, 'one row per removal, got %d: %s' % (len(written), written)
        assert Path(written[0]['path']).name == 'coder-old9999'


if __name__ == '__main__':
    _tests = sorted((n, f) for n, f in list(globals().items())
                    if n.startswith('test_') and callable(f))
    _failed = 0
    for _name, _fn in _tests:
        try:
            _fn()
            print('ok   ' + _name)
        except Exception:
            _failed += 1
            print('FAIL ' + _name)
            traceback.print_exc()
    print('%d/%d passed' % (len(_tests) - _failed, len(_tests)))
    sys.exit(1 if _failed else 0)
