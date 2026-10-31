'''t2151: one shared sweep result store, keyed by (tree content, environment, suite).

WHY. scripts/run_tests.py caches per-suite passes in workspace/suite-cache.json, keyed by
suite_key(path) -- a suite's OWN inputs. coder.py's run_suites runs its ~373-suite baseline in a
fresh sandbox and reads none of that cache, so every dispatch re-runs all of them even when main
has not changed. One store keyed by (tree content hash, environment hash, suite name) lets the
sandbox ask for its names first and run only the misses.

THE TRAP THIS SUITE PINS. A pass recorded in the live tree is NOT evidence about a coder.py
sandbox whose EXCLUDE set leaves resources out: test_notify.py needs py_vapid, which lives in
.venv, and workspace/sandbox-baseline.json exists because suites really do pass live and fail in
the sandbox. A store keyed on the tree ALONE would hand the sandbox a green that is false in it.
So the environment is part of the key, and this suite checks the key from BOTH directions:
recorded for A, returned for A, absent for B.

RED ON MAIN: scripts/run_tests.py has no sandbox_key, no tree_id_of, no sweep_results, no
record_sweep_results, no SWEEP_STORE and no ENV_LIVE, so the first call raises AttributeError.

Every write goes to a tempfile path passed in as path=, or via SWEEP_RESULT_STORE pointed at a
temp dir. Nothing is read from, or written to, the repository's own store.
'''
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_tests as rt  # noqa: E402

BIG = {'.git', 'chat', 'node_modules', '__pycache__', '.venv', 'build', '.gradle'}
SMALL = {'.git'}
NAMES = ['test_a.py', 'test_b.py']


def _file(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


def test_sandbox_key_follows_the_exclude_set():
    big = rt.sandbox_key(BIG)
    small = rt.sandbox_key(SMALL)
    assert len(big) == 16, 'sandbox_key must be a 16-hex id, got ' + repr(big)
    assert big != small, ('a sandbox that excludes .venv must not key the same as one that '
                          'excludes .git only: a pass in one is not evidence for the other')
    assert big == rt.sandbox_key(sorted(BIG)), 'the order of the names must not change the key'
    assert rt.ENV_LIVE, 'the live tree needs its own environment id'
    assert rt.ENV_LIVE != big, 'live is not a sandbox: a live pass must not be reused as one'
    assert rt.ENV_LIVE != rt.sandbox_key(sorted(BIG))


def test_tree_id_of_hashes_a_tree_that_has_no_git():
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        ra, rb = Path(a), Path(b)
        _file(ra, 'scripts/s.py', 'X = 1' + chr(10))
        _file(rb, 'scripts/s.py', 'X = 1' + chr(10))
        assert rt.tree_id_of(ra) == rt.tree_id_of(rb), 'identical bytes must hash identical'
        _file(rb, 'scripts/s.py', 'X = 2' + chr(10))
        assert rt.tree_id_of(ra) != rt.tree_id_of(rb), 'an edit must change the id'
        _file(rb, 'scripts/s.py', 'X = 1' + chr(10))
        _file(rb, '.venv/lib/vapid.py', 'X = 1' + chr(10))
        assert rt.tree_id_of(ra) == rt.tree_id_of(rb), ('a skipped directory must not change the id: '
                                                       'a sandbox must hash like the tree copied from')
        _file(rb, 'extra/file.py', 'X = 1' + chr(10))
        assert rt.tree_id_of(ra) != rt.tree_id_of(rb), 'an added file must change the id'


def test_a_pass_is_reused_only_for_its_own_tree_and_environment():
    with tempfile.TemporaryDirectory() as d:
        store = Path(d) / 'store.json'
        rt.record_sweep_results('treeA', 'envA', [('test_a.py', 0, 1.0, 'ok')], path=store)
        got = rt.sweep_results('treeA', 'envA', NAMES, path=store)
        assert list(got) == ['test_a.py'], ('only the suite recorded comes back; test_b.py has no '
                                           'entry and must run: ' + repr(got))
        assert got['test_a.py']['rc'] == 0, 'a hit must carry the verdict it was recorded with'
        assert got['test_a.py']['secs'] == 1.0, 'a hit must carry the seconds it took'
        assert list(rt.sweep_results('treeB', 'envA', NAMES, path=store)) == [], \
            'a pass recorded on treeA is not evidence about treeB'
        assert list(rt.sweep_results('treeA', 'envB', NAMES, path=store)) == [], \
            'a pass recorded in envA is not evidence about envB -- the .venv case'
        assert list(rt.sweep_results('treeA', 'envA', ['test_z.py'], path=store)) == [], \
            'a name with no entry is a miss, not a verdict'
        disk = json.loads(store.read_text(encoding='utf-8'))
        assert list(disk) == ['treeA|envA|test_a.py'], ('the key on disk is tree, environment and '
                                                      'suite joined by a pipe: ' + repr(list(disk)))
        assert disk['treeA|envA|test_a.py']['rc'] == 0, 'rc must be stored as a number, not a word'


def test_a_failure_retires_the_pass_it_witnessed():
    with tempfile.TemporaryDirectory() as d:
        store = Path(d) / 'store.json'
        rt.record_sweep_results('treeA', 'envA', [('test_a.py', 0, 1.0, 'ok')], path=store)
        rt.record_sweep_results('treeA', 'envA',
                                [('test_a.py', 1, 2.0, 'noise line' + chr(10) + 'AssertionError: boom')],
                                path=store)
        assert list(rt.sweep_results('treeA', 'envA', NAMES, path=store)) == [], \
            'a suite that ran and failed on this tree must not be reused'
        disk = json.loads(store.read_text(encoding='utf-8'))
        assert disk['treeA|envA|test_a.py']['rc'] == 1, 'the failure is recorded, not dropped'
        assert 'boom' in disk['treeA|envA|test_a.py']['out'], \
            'a red row keeps its cause, bounded, so it can be read without re-running it'
        rt.record_sweep_results('treeA', 'envA', [('test_a.py', 0, 0.5, 'ok')], path=store)
        assert list(rt.sweep_results('treeA', 'envA', NAMES, path=store)) == ['test_a.py'], \
            'a later pass on the same tree clears the failure'


def test_recording_one_suite_leaves_another_alone():
    with tempfile.TemporaryDirectory() as d:
        store = Path(d) / 'store.json'
        rt.record_sweep_results('treeA', 'envA', [('test_a.py', 0, 1.0, 'ok')], path=store)
        rt.record_sweep_results('treeA', 'envA', [('test_b.py', 0, 2.0, 'ok')], path=store)
        assert sorted(rt.sweep_results('treeA', 'envA', NAMES, path=store)) == ['test_a.py', 'test_b.py'], \
            'recording a second suite must not erase the first'


def test_an_unreadable_store_reuses_nothing_and_says_so():
    with tempfile.TemporaryDirectory() as d:
        store = Path(d) / 'store.json'
        store.write_text('{not json', encoding='utf-8')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            got = rt.sweep_results('treeA', 'envA', NAMES, path=store)
        assert list(got) == [], 'a store that cannot be parsed must reuse nothing'
        assert 'unreadable' in buf.getvalue(), \
            'the miss must be SAID, in the words the existing cache already uses, not silent'


def test_a_missing_store_is_a_miss_not_an_error():
    with tempfile.TemporaryDirectory() as d:
        store = Path(d) / 'nothing-here.json'
        assert list(rt.sweep_results('treeA', 'envA', NAMES, path=store)) == [], \
            'no store at all is a miss for every suite, not a crash'
        assert not store.exists(), 'reading must not create the store'


def test_the_store_is_a_file_of_its_own_and_a_writer_can_repoint_it():
    assert rt.SWEEP_STORE.name == 'sweep-results.json', \
        'the shared store is its own file: reusing suite-cache.json puts two keying rules in one file'
    assert rt.SWEEP_STORE.parent == rt.REPO / 'workspace', 'it lives under the repository workspace'
    assert rt.SWEEP_STORE != rt.CACHE, 'the sweep store is not the suite cache'
    with tempfile.TemporaryDirectory() as d:
        mine = Path(d) / 'mine.json'
        os.environ['SWEEP_RESULT_STORE'] = str(mine)
        try:
            rt.record_sweep_results('t', 'e', [('test_a.py', 0, 1.0, 'ok')])
            assert list(rt.sweep_results('t', 'e', ['test_a.py'])) == ['test_a.py'], \
                'SWEEP_RESULT_STORE must steer the reader as well as the writer'
        finally:
            os.environ.pop('SWEEP_RESULT_STORE', None)
        assert mine.is_file(), 'the variable moved the default path, not just a test path'


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
    print(str(len(fns)) + ' sweep result store checks passed')
