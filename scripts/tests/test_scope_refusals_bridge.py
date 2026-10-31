#!/usr/bin/env python3
'''t1356: every bridge refusal is recorded in workspace/scope-refusals.jsonl.

RED ON MAIN for the missing change: scripts/bridge.py prints REFUSED per error and exits 2 in main
and writes no ledger row anywhere -- workspace/scope-refusals.jsonl does not exist in the
repository today, so the first ledger assertion below fails on main and passes once a writer is
called at that refusal point.

What the assertions pin (behaviour, not shape):
  1. a refused chunk writes a bridge-refused row whose reason carries the validation text whole
     (t1316 died to a chunk missing done_when; a row that paraphrases the reason is useless);
  2. every refusal reason gets its own row -- two invalid chunks give at least two rows;
  3. a bridge refusal is not a size finding, so sites and cap are recorded 0, not invented;
  4. append-only: a second refusal leaves the rows of the first run intact at the head;
  5. refusals only: a block that plans cleanly writes no row;
  6. the ledger lands under the repo given by --repo, and the real repository copy is
     byte-identical before and after.

No live state (tempfile.mkdtemp only), no model call, no process left running, every
subprocess.run carries timeout=, and no --apply is used: a dry run refuses before it writes a
brief, so the fixture repository stays clean. Run: python3 scripts/tests/test_scope_refusals_bridge.py
'''
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'scripts' / 'bridge.py'
NL = chr(10)


def _chunks_block(chunks):
    return '```chunks' + NL + json.dumps(chunks, indent=1) + NL + '```' + NL


def _repo(tmp):
    repo = Path(tmp) / 'repo'
    (repo / 'scripts' / 'tests').mkdir(parents=True)
    (repo / 'scripts' / 'foo.py').write_text('def a():' + NL + '    return 1' + NL, encoding='utf-8')
    (repo / 'scripts' / 'tests' / 'test_t.py').write_text('print(1)' + NL, encoding='utf-8')
    return repo


def _answer(tmp, chunks):
    p = Path(tmp) / 'answer.md'
    p.write_text('# scope' + NL + NL + _chunks_block(chunks), encoding='utf-8')
    return p


def _bridge(repo, answer):
    # no --apply: a dry run refuses before it writes a brief, so the fixture repo stays clean
    return subprocess.run([sys.executable, '-s', str(SCRIPT), str(answer), '--task', 't1356',
                           '--repo', str(repo)],
                          cwd=str(repo), capture_output=True, text=True, timeout=180)


def _rows(repo):
    p = repo / 'workspace' / 'scope-refusals.jsonl'
    if not p.is_file():
        return None
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]


def _valid_chunk():
    return {'kind': 'implementation', 'file': 'scripts/foo.py', 'target': 'scripts/tests/test_t.py',
            'single_change': 'give function a a docstring',
            'what_is_wrong': 'it has no docstring at all',
            'done_when': 'python3 scripts/tests/test_t.py exits 0',
            'related_entries': ['t1356']}


def _chunk_missing(field):
    c = _valid_chunk()
    del c[field]
    return c


def test_a_a_refused_chunk_is_recorded():
    tmp = tempfile.mkdtemp()
    repo = _repo(tmp)
    answer = _answer(tmp, [_chunk_missing('done_when')])
    r = _bridge(repo, answer)
    assert r.returncode == 2, 'bridge must refuse a chunk with no done_when (exit 2), got %r' % (
        r.returncode,)
    rows = _rows(repo)
    assert rows is not None, ('a refused block wrote no ledger row: workspace/scope-refusals.jsonl '
                              'is absent after a refusal (exit 2): %s' % r.stdout[-300:])
    hits = [x for x in rows if x.get('rule') == 'bridge-refused']
    assert len(hits) >= 1, 'expected a bridge-refused row, got %r' % (rows,)
    row = hits[0]
    assert row.get('task') == 't1356', 'row.task is %r, expected t1356' % (row.get('task'),)
    assert 'done_when' in str(row.get('reason', '')), (
        'row.reason must carry the refusal reason whole, got %r' % (row.get('reason'),))
    assert row.get('ts'), 'row.ts is empty: a refusal with no timestamp cannot be ordered'
    assert row.get('rescoped') is False, 'row.rescoped must be False at refusal time, got %r' % (
        row.get('rescoped'),)
    assert row.get('built') is False, 'row.built must be False at refusal time, got %r' % (
        row.get('built'),)
    assert row.get('sites') == 0 and row.get('cap') == 0, (
        'a bridge refusal is not a size finding: sites and cap are 0, got sites=%r cap=%r' % (
            row.get('sites'), row.get('cap')))


def test_b_every_reason_gets_its_own_row():
    tmp = tempfile.mkdtemp()
    repo = _repo(tmp)
    answer = _answer(tmp, [_chunk_missing('done_when'), _chunk_missing('target')])
    r = _bridge(repo, answer)
    assert r.returncode == 2, 'expected exit 2, got %r' % (r.returncode,)
    rows = _rows(repo)
    assert rows is not None, 'a refused block wrote no ledger row at all'
    reasons = [str(x.get('reason', '')) for x in rows if x.get('rule') == 'bridge-refused']
    assert len(reasons) >= 2, ('one row per refusal reason: two invalid chunks produced %d rows: %r'
                               % (len(reasons), rows))
    assert any('done_when' in s for s in reasons) and any('target' in s for s in reasons), (
        'each row must carry the reason it records, got %r' % (reasons,))


def test_c_a_valid_block_records_nothing():
    tmp = tempfile.mkdtemp()
    repo = _repo(tmp)
    answer = _answer(tmp, [_valid_chunk()])
    r = _bridge(repo, answer)
    assert r.returncode == 0, 'a valid chunk must plan cleanly (exit 0), got %r: %s' % (
        r.returncode, r.stdout[-300:])
    assert _rows(repo) is None, 'bridge wrote a refusal row for a block it accepted: refusals only'


def test_d_the_ledger_is_append_only():
    tmp = tempfile.mkdtemp()
    repo = _repo(tmp)
    answer = _answer(tmp, [_chunk_missing('done_when')])
    _bridge(repo, answer)
    first = _rows(repo)
    assert first, 'the first refusal wrote no row'
    _bridge(repo, answer)
    after = _rows(repo)
    assert len(after) == 2 * len(first), (
        'a second refusal must append, not overwrite: %d rows before, %d after' % (
            len(first), len(after)))
    assert after[:len(first)] == first, 'the second run rewrote the rows of the first run'


def test_e_the_live_ledger_is_untouched():
    live = REPO / 'workspace' / 'scope-refusals.jsonl'
    before = live.read_bytes() if live.is_file() else None
    tmp = tempfile.mkdtemp()
    repo = _repo(tmp)
    answer = _answer(tmp, [_chunk_missing('done_when')])
    _bridge(repo, answer)
    after = live.read_bytes() if live.is_file() else None
    assert after == before, ('the run wrote into the live workspace/scope-refusals.jsonl: the '
                            'writer must use the repo it was given (--repo)')


if __name__ == '__main__':
    _tests = [test_a_a_refused_chunk_is_recorded, test_b_every_reason_gets_its_own_row,
              test_c_a_valid_block_records_nothing, test_d_the_ledger_is_append_only,
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
