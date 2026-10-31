#!/usr/bin/env python3
'''t1356: every scope_lint refusal is recorded in workspace/scope-refusals.jsonl.

RED ON MAIN for the missing change: scripts/scope_lint.py prints one line per finding and exits 2,
and writes no ledger row anywhere -- workspace/scope-refusals.jsonl does not exist in the
repository today, so test_a fails at its first ledger assertion on main and passes once a writer
is called at that refusal point.

What the assertions pin (behaviour, not shape):
  1. a brief-scatter refusal writes a row whose rule is brief-scatter, whose sites equals the 7
     edit sites of the fixture and whose cap equals scope_lint.MAX_EDIT_SITES (the real constant,
     not a literal), with the refusal evidence stored whole in reason -- a sliced reason fails;
  2. two scattered chunks in one run write two rows, each naming the file the refusal was about;
  3. append-only: a second refusal leaves the rows of the first run intact at the head;
  4. refusals only: a block that validates writes no row at all;
  5. the ledger lands under the repo given by --repo; the real repository copy is byte-identical
     before and after.

No live state (tempfile.mkdtemp only), no model call, no process left running, every
subprocess.run carries timeout=. Run: python3 scripts/tests/test_scope_refusals_lint.py
'''
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'scripts' / 'scope_lint.py'
sys.path.insert(0, str(REPO / 'scripts'))
import scope_lint

NL = chr(10)
SEVEN = ['f0', 'f1', 'f2', 'f3', 'f4', 'f5', 'f6']
FOO = ''.join(('def f' + str(i) + '():' + NL + '    return ' + str(i) + NL) for i in range(7))


def _chunks_block(chunks):
    return '```chunks' + NL + json.dumps(chunks, indent=1) + NL + '```' + NL


def _repo(tmp):
    repo = Path(tmp) / 'repo'
    (repo / 'scripts' / 'tests').mkdir(parents=True)
    (repo / 'scripts' / 'foo.py').write_text(FOO, encoding='utf-8')
    (repo / 'scripts' / 'bar.py').write_text(FOO, encoding='utf-8')
    (repo / 'scripts' / 'tests' / 'test_t.py').write_text('print(1)' + NL, encoding='utf-8')
    (repo / 'scripts' / 'tests' / 'test_u.py').write_text('print(1)' + NL, encoding='utf-8')
    return repo


def _chunk(file_name, target_name, symbols):
    return {'kind': 'implementation', 'file': file_name, 'target': target_name,
            'single_change': 'give every function in the file a one-line docstring',
            'what_is_wrong': 'the functions have no docstrings at all',
            'done_when': 'python3 ' + target_name + ' exits 0',
            'related_entries': ['t1356'], 'symbols': symbols}


def _answer(tmp, chunks):
    p = Path(tmp) / 'answer.md'
    p.write_text('# scope' + NL + NL + _chunks_block(chunks), encoding='utf-8')
    return p


def _lint(repo, answer):
    return subprocess.run([sys.executable, '-s', str(SCRIPT), str(answer), '--task', 't1356',
                           '--no-run', '--repo', str(repo)],
                          cwd=str(repo), capture_output=True, text=True, timeout=180)


def _rows(repo):
    p = repo / 'workspace' / 'scope-refusals.jsonl'
    if not p.is_file():
        return None
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]


def test_a_scatter_refusal_writes_a_row():
    tmp = tempfile.mkdtemp()
    repo = _repo(tmp)
    answer = _answer(tmp, [_chunk('scripts/foo.py', 'scripts/tests/test_t.py', SEVEN)])
    r = _lint(repo, answer)
    assert r.returncode == 2, 'scope_lint must refuse a 7-site chunk (exit 2), got %r: %s' % (
        r.returncode, r.stdout[-300:])
    rows = _rows(repo)
    assert rows is not None, ('a refused scope wrote no ledger row: workspace/scope-refusals.jsonl '
                              'is absent after a refusal (exit 2). stdout: %s' % r.stdout[-300:])
    hits = [x for x in rows if x.get('rule') == 'brief-scatter']
    assert len(hits) == 1, 'expected exactly one brief-scatter row, got %d: %r' % (len(hits), rows)
    row = hits[0]
    assert row.get('task') == 't1356', 'row.task is %r, expected t1356' % (row.get('task'),)
    assert row.get('sites') == 7, 'row.sites is %r, expected the 7 edit sites of the fixture' % (
        row.get('sites'),)
    assert row.get('cap') == scope_lint.MAX_EDIT_SITES, 'row.cap is %r, expected MAX_EDIT_SITES=%d' % (
        row.get('cap'), scope_lint.MAX_EDIT_SITES)
    assert row.get('ts'), 'row.ts is empty: a refusal with no timestamp cannot be ordered'
    assert row.get('rescoped') is False, 'row.rescoped must be False at refusal time, got %r' % (
        row.get('rescoped'),)
    assert row.get('built') is False, 'row.built must be False at refusal time, got %r' % (
        row.get('built'),)
    assert 'output cap' in str(row.get('reason', '')), (
        'row.reason must carry the refusal evidence whole, got %r' % (row.get('reason'),))


def test_b_two_refusals_write_two_rows():
    tmp = tempfile.mkdtemp()
    repo = _repo(tmp)
    answer = _answer(tmp, [_chunk('scripts/foo.py', 'scripts/tests/test_t.py', SEVEN),
                           _chunk('scripts/bar.py', 'scripts/tests/test_u.py', SEVEN)])
    r = _lint(repo, answer)
    assert r.returncode == 2, 'expected exit 2, got %r' % (r.returncode,)
    rows = _rows(repo)
    assert rows is not None, 'a refused scope wrote no ledger row at all'
    scatter = [x for x in rows if x.get('rule') == 'brief-scatter']
    assert len(scatter) >= 2, ('one row per refusal: two scattered chunks produced %d rows: %r'
                               % (len(scatter), rows))
    assert {x.get('file') for x in scatter} == {'scripts/foo.py', 'scripts/bar.py'}, (
        'each row must say which file the refusal was about, got %r' % sorted(
            {x.get('file') for x in scatter}))


def test_c_ledger_is_append_only():
    tmp = tempfile.mkdtemp()
    repo = _repo(tmp)
    answer = _answer(tmp, [_chunk('scripts/foo.py', 'scripts/tests/test_t.py', SEVEN)])
    _lint(repo, answer)
    first = _rows(repo)
    assert first, 'the first refusal wrote no row'
    _lint(repo, answer)
    after = _rows(repo)
    assert len(after) == 2 * len(first), (
        'a second refusal must append, not overwrite: %d rows before, %d after' % (
            len(first), len(after)))
    assert after[:len(first)] == first, 'the second run rewrote the rows of the first run'


def test_d_a_clean_block_records_nothing():
    tmp = tempfile.mkdtemp()
    repo = _repo(tmp)
    answer = _answer(tmp, [_chunk('scripts/foo.py', 'scripts/tests/test_t.py', ['f0'])])
    r = _lint(repo, answer)
    assert r.returncode == 0, 'a 1-site chunk must validate (exit 0), got %r: %s' % (
        r.returncode, r.stdout[-300:])
    assert _rows(repo) is None, ('scope_lint wrote a refusal row for a block it accepted: the '
                                 'ledger records refusals only')


def test_e_the_live_ledger_is_untouched():
    live = REPO / 'workspace' / 'scope-refusals.jsonl'
    before = live.read_bytes() if live.is_file() else None
    tmp = tempfile.mkdtemp()
    repo = _repo(tmp)
    answer = _answer(tmp, [_chunk('scripts/foo.py', 'scripts/tests/test_t.py', SEVEN)])
    _lint(repo, answer)
    after = live.read_bytes() if live.is_file() else None
    assert after == before, ('the run wrote into the live workspace/scope-refusals.jsonl: the '
                            'writer must use the repo it was given (--repo)')


if __name__ == '__main__':
    _tests = [test_a_scatter_refusal_writes_a_row, test_b_two_refusals_write_two_rows,
              test_c_ledger_is_append_only, test_d_a_clean_block_records_nothing,
              test_e_the_live_ledger_is_untouched]
    _bad = []
    for _t in _tests:
        try:
            _t()
            print('ok   %s' % _t.__name__)
        except Exception as _exc:
            print('FAIL %s: %s: %s' % (_t.__name__, type(_exc).__name__, _exc))
            _bad.append(_t.__name__)
    print('%d/%d passed' % (len(_tests) - len(_bad), len(_tests)))
    raise SystemExit(1 if _bad else 0)
