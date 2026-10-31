#!/usr/bin/env python3
'''t805 -- the suite-state ledger: one row per suite per run, and a red row cannot be ownerless.

WHY: workspace/tasks/t805--ledger-every-suite-s-red-green-state-on-main-wit.md quotes two findings
that ended unowned. Task briefs tolerate 26-53 suites 'already failing', so 'fails on main too'
lands inside the normalised noise floor and reaches no one, and the endpoint that runs suites
(scripts/run_tests.py, the _tick function) records no per-suite red/green row bound to a commit and
an owner, so a red suite is not addressable and has no owner to route to.

RED ON MAIN: scripts/run_tests.py has no record_suite_state, no suite_owner and no SUITE_STATE
constant, so every test below fails on exactly the missing change.

WHY test_5 SETS _TREE: _tick writes its existing sweep-journal row only when the module global
_TREE is truthy, and main() is what sets it; an imported module has _TREE = None. The test sets it
so the journal endpoint is exercised the way it runs in a real sweep, then restores it.

No pytest (it is not installed in this repository) -- run it as:
    python3 scripts/tests/test_t805_suite_state_ledger.py
No live state is touched: every write goes inside a per-test tempfile.TemporaryDirectory, so the
repo tree and workspace/suite-state.jsonl are never written by this file.
'''
import json
import re
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import run_tests  # noqa: E402

ISO = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
SHA = re.compile(r'^[0-9a-f]{8,40}$')

# REAL suite names from the finding (workspace/rca/06aaf666a517.md), not placeholders: two of them
# are red and green in the same test so a stub that returns one constant value cannot pass.
GREEN = 'test_console_reconcile.py'
RED = 'test_console_read_numbered.py'
OTHER = 'test_ask_q16_sessions.py'


def _rows(path):
    '''Every JSONL row in a ledger file. A missing file is a ledger with nothing in it.'''
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]


def _point_at(tmp):
    '''Repoint the ledger, the sweep journal, the owner registry AND the task store into the temp
    dir. The store is here because an owner only counts while its task is OPEN (t1886): a fixture
    that names t799 without providing it would pass for the wrong reason.'''
    sp = tmp / 'suite-state.jsonl'
    run_tests.SUITE_STATE = sp
    run_tests.JOURNAL = tmp / 'journal.jsonl'
    run_tests.SUITE_OWNERS = tmp / 'suite-owners.json'
    run_tests.TASK_DIR = tmp / 'tasks'
    run_tests.TASK_DIR.mkdir(exist_ok=True)
    for tid in ('t33', 't799'):
        (run_tests.TASK_DIR / (tid + '--open.md')).write_text('---\nid: %s\nstatus: queued\n---\n' % tid,
                                                             encoding='utf-8')
    return sp


def test_1_a_green_run_is_recorded_with_real_values(tmp):
    sp = _point_at(tmp)
    res = run_tests.record_suite_state(GREEN, 0, 12.5, owner='t805')
    assert res['written'] is True, 'the ledger row was not written: %r' % (res,)
    rows = _rows(sp)
    assert len(rows) == 1, 'expected exactly 1 ledger row, got %d: %r' % (len(rows), rows)
    r = rows[0]
    assert r['suite'] == GREEN, r
    assert r['exit_code'] == 0, 'exit_code must be the suite real rc, not a shape: %r' % (r,)
    assert r['seconds'] == 12.5, 'seconds must be the measured seconds: %r' % (r,)
    assert r['owner'] == 't805', 'the owner handed in must be recorded verbatim: %r' % (r,)
    assert r['task_ref'] == 't805', 'task_ref must carry the owner id: %r' % (r,)
    assert r['unowned'] is False, r
    assert r['command'] == 'python3 scripts/tests/' + GREEN, (
        'the row must say how to reproduce it, built from the suite name: %r' % (r.get('command'),))
    assert ISO.match(str(r.get('ts', ''))), 'ts must be a UTC stamp ending Z: %r' % (r.get('ts'),)
    assert r.get('commit'), 'every row is pinned to the commit it ran at: %r' % (r,)
    assert r['commit_source'] in ('git', 'git-fallback'), r
    assert SHA.match(str(r['commit'])) or str(r['commit']).startswith('tree:'), (
        'commit must be a sha, or a loud tree: fallback: %r' % (r,))


def test_2_a_red_suite_with_no_owner_is_recorded_and_shouted(tmp):
    sp = _point_at(tmp)
    err = StringIO()
    with redirect_stderr(err):
        res = run_tests.record_suite_state(RED, 1, 3.0)
    assert res['written'] is True, (
        'a red suite with no owner must still be recorded, never dropped: %r' % (res,))
    assert res['unowned'] is True, 'a red row with an empty owner is UNOWNED: %r' % (res,)
    rows = _rows(sp)
    assert len(rows) == 1, 'expected 1 ledger row, got %d: %r' % (len(rows), rows)
    assert rows[0]['exit_code'] == 1, rows[0]
    assert rows[0]['owner'] == 'UNOWNED', (
        'an ownerless red must be stamped UNOWNED so it stays addressable: %r' % (rows[0],))
    text = err.getvalue()
    assert 'UNOWNED' in text and RED in text, (
        'an ownerless red must be printed at the endpoint that records it, not left to be '
        'discovered later: %r' % (text,))


def test_3_the_ledger_appends_and_keeps_distinct_values(tmp):
    sp = _point_at(tmp)
    run_tests.record_suite_state(GREEN, 0, 4.0, owner='t805')
    run_tests.record_suite_state(OTHER, 1, 2.0, owner='t799')
    rows = _rows(sp)
    assert len(rows) == 2, 'the ledger APPENDS one row per suite per run: got %d rows' % (len(rows),)
    by_name = {r['suite']: r for r in rows}
    assert set(by_name) == {GREEN, OTHER}, by_name
    assert by_name[GREEN]['exit_code'] == 0 and by_name[OTHER]['exit_code'] == 1, (
        'each row carries its own suite verdict, not one shared value: %r' % (rows,))
    assert by_name[GREEN]['owner'] == 't805' and by_name[OTHER]['owner'] == 't799', (
        'each row carries its own owner, not one shared value: %r' % (rows,))
    assert by_name[GREEN]['seconds'] == 4.0 and by_name[OTHER]['seconds'] == 2.0, rows


def test_4_the_owner_registry_is_what_makes_a_red_addressable(tmp):
    _point_at(tmp)
    reg = tmp / 'suite-owners.json'
    reg.write_text(json.dumps({GREEN: 't799-console-compliance', RED: 't33-console-pin'}),
                   encoding='utf-8')
    run_tests.SUITE_OWNERS = reg
    assert run_tests.suite_owner(GREEN) == 't799-console-compliance', (
        'a listed suite resolves to its declared owner')
    assert run_tests.suite_owner(RED) == 't33-console-pin', (
        'two listed suites must resolve to two distinct owners')
    assert run_tests.suite_owner('test_not_in_the_registry.py') == '', (
        'an unlisted suite has no owner; it must not inherit one')
    run_tests.SUITE_OWNERS = tmp / 'absent-owners.json'
    assert run_tests.suite_owner(GREEN) == '', (
        'a missing registry means no owner recorded, and it must not raise')
    broken = tmp / 'broken-owners.json'
    broken.write_text('{not json', encoding='utf-8')
    run_tests.SUITE_OWNERS = broken
    assert run_tests.suite_owner(GREEN) == '', (
        'an unreadable registry is no owner, not a crash: the runner must survive it')


def test_5_the_suite_endpoint_writes_the_ledger_row(tmp):
    sp = _point_at(tmp)
    journal = run_tests.JOURNAL
    tree = run_tests._TREE
    run_tests._TREE = 't805-fixture-tree'
    try:
        run_tests._tick(RED, 1, 2.0)
    finally:
        run_tests._TREE = tree
    rows = _rows(sp)
    assert len(rows) == 1, (
        'the endpoint that runs a suite must record its state: %d rows' % (len(rows),))
    r = rows[0]
    assert r['suite'] == RED, r
    assert r['exit_code'] == 1, r
    assert r['seconds'] == 2.0, r
    assert r['owner'] == 'UNOWNED', (
        'the runner knows no owner of its own, so a red it runs is UNOWNED: %r' % (r,))
    assert r.get('commit'), r
    jrows = _rows(journal)
    assert len(jrows) == 1 and jrows[0]['kind'] == 'suite' and jrows[0]['rc'] == 1 \
        and jrows[0]['suite'] == RED and jrows[0]['tree'] == 't805-fixture-tree', (
        'the existing sweep-journal row must survive this change, stamped as before: %r' % (jrows,))


def test_6_an_unwritable_ledger_says_so_and_never_raises(tmp):
    blocker = tmp / 'blocker'
    blocker.write_text('not a directory', encoding='utf-8')
    run_tests.SUITE_STATE = blocker / 'suite-state.jsonl'
    err = StringIO()
    with redirect_stderr(err):
        res = run_tests.record_suite_state(GREEN, 0, 1.0)
    assert res['written'] is False, (
        'the return value must say the row was NOT written: %r' % (res,))
    text = err.getvalue()
    assert 'suite-state' in text, 'the failure must name the ledger file: %r' % (text,)
    assert text.strip(), 'a ledger that cannot be written must be reported, never silent'


def test_7_the_commit_is_the_real_head(tmp):
    sp = _point_at(tmp)
    head = ''
    try:
        proc = subprocess.run(['git', '-C', str(REPO), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, timeout=30)
        head = proc.stdout.strip() if proc.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError) as e:
        print('  NOTE: git rev-parse unavailable (%s); the stamp falls back' % (e,))
    run_tests.record_suite_state(GREEN, 0, 1.0, owner='t805')
    rows = _rows(sp)
    assert rows, 'a recorded run leaves a row'
    row = rows[0]
    assert row['commit_source'] in ('git', 'git-fallback'), row
    if head and SHA.match(head) and row['commit_source'] == 'git':
        assert row['commit'] == head, (
            'the row must carry this repo HEAD sha %s, got %r' % (head, row['commit']))


def main():
    tests = sorted((v for k, v in globals().items()
                    if k.startswith('test_') and callable(v)), key=lambda f: f.__name__)
    failed = 0
    for fn in tests:
        with tempfile.TemporaryDirectory(prefix='t805-ledger-') as td:
            try:
                fn(Path(td))
                print('  ok   %s' % (fn.__name__,))
            except AssertionError as e:
                failed += 1
                print('  FAIL %s: %s' % (fn.__name__, e))
            except Exception as e:
                failed += 1
                print('  ERROR %s: %s: %s' % (fn.__name__, type(e).__name__, e))
    print('\n  %d/%d passed' % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
