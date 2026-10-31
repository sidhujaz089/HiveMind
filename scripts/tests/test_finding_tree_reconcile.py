#!/usr/bin/env python3
'''t1083 -- a session-review finding may not be stored unresolved while the tree it describes
no longer contains the defect it names.

Red on main for the missing change and for it alone: scripts/session_review.py defines no
reconcile_finding, no reconcile_violations and no enters_open_pool, so every test below dies with
AttributeError naming one of those three names, and nothing else. Read off the rows on disk: a
finding row carries kind, disposition, covered_by, where, why_missed, key -- and no field able to
say the defect is no longer in the tree. That is how RCA 9c0e0a183707 filed a fix that had landed
seven weeks earlier: its locator is a probe.py line number, the signature SYSTEM.replace( is
absent from the tree, and the row is kind:unresolved with covered_by empty.

No model, no server, no live state: every tree is a TemporaryDirectory, every subprocess call
carries a timeout, nothing here reads workspace/session-reviews/.
'''
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import session_review as sr  # noqa: E402

# The two signatures the RCA named, as two DISTINCT values in two DISTINCT files: a build that
# answers one canned value for every input cannot pass the first two tests.
SIG_SYNC = 'SYSTEM.replace('
SIG_CONSTANT = 'num_ctx=65536'
SIG_ABSENT = 'num_ctx=99999'


def _git(root, *args):
    return subprocess.run(
        ['git', '-C', str(root), '-c', 'user.email=t@t', '-c', 'user.name=t',
         '-c', 'commit.gpgsign=false'] + list(args),
        capture_output=True, text=True, timeout=30)


def _body(payload, at_line):
    '''File text whose at_line-th (1-based) line contains payload verbatim.'''
    lines = ['padding %d' % i for i in range(1, at_line)]
    lines.append('SETTING = %r' % payload)
    lines.append('tail after')
    return '\n'.join(lines) + '\n'


def _tree(tmp):
    '''A throwaway tree: probe.py carries SIG_SYNC on line 7, settings.py SIG_CONSTANT on line 3.'''
    (tmp / 'pkg').mkdir()
    (tmp / 'pkg' / 'probe.py').write_text(_body(SIG_SYNC, 7))
    (tmp / 'pkg' / 'settings.py').write_text(_body(SIG_CONSTANT, 3))
    return tmp


def test_reports_the_signature_and_where_it_is():
    with tempfile.TemporaryDirectory() as td:
        root = _tree(Path(td))
        stamp = sr.reconcile_finding(SIG_SYNC, root)
        assert stamp['resolved_in_tree'] is False, stamp
        assert stamp['reconciled_against']['signature'] == SIG_SYNC, stamp
        assert stamp['reconciled_against']['hits'] == [{'file': 'pkg/probe.py', 'line': 7}], stamp


def test_lists_every_home_the_signature_has():
    # The RCA's own anti-pattern is sync-the-copy, not delete-the-copy: a second home of the
    # signature must be visible, not the first hit only.
    with tempfile.TemporaryDirectory() as td:
        root = _tree(Path(td))
        (root / 'pkg' / 'copy.py').write_text(_body(SIG_SYNC, 2))
        stamp = sr.reconcile_finding(SIG_SYNC, root)
        assert stamp['resolved_in_tree'] is False, stamp
        assert stamp['reconciled_against']['hits'] == [
            {'file': 'pkg/copy.py', 'line': 2},
            {'file': 'pkg/probe.py', 'line': 7},
        ], stamp


def test_says_resolved_when_the_signature_is_absent():
    with tempfile.TemporaryDirectory() as td:
        stamp = sr.reconcile_finding(SIG_ABSENT, _tree(Path(td)))
        assert stamp['resolved_in_tree'] is True, stamp
        assert stamp['reconciled_against']['hits'] == [], stamp


def test_refuses_to_call_an_empty_signature_resolved():
    # An empty signature proves nothing: a row that searched for nothing cannot claim the defect
    # is gone, or every unsigned row leaves the open pool silently.
    with tempfile.TemporaryDirectory() as td:
        assert sr.reconcile_finding('', _tree(Path(td)))['resolved_in_tree'] is False
        assert sr.reconcile_finding('   ', _tree(Path(td)))['resolved_in_tree'] is False


def test_skips_the_git_directory():
    # The locator is a search of the tree. .git is history, not the tree.
    with tempfile.TemporaryDirectory() as td:
        root = _tree(Path(td))
        (root / '.git' / 'hooks').mkdir(parents=True)
        (root / '.git' / 'hooks' / 'probe.py').write_text(_body(SIG_SYNC, 1))
        stamp = sr.reconcile_finding(SIG_SYNC, root)
        assert stamp['resolved_in_tree'] is True, stamp
        assert stamp['reconciled_against']['hits'] == [], stamp


def test_stamps_the_commit_it_read():
    with tempfile.TemporaryDirectory() as td:
        root = _tree(Path(td))
        assert _git(root, 'init', '-q').returncode == 0
        assert _git(root, 'add', '-A').returncode == 0
        assert _git(root, 'commit', '-q', '-m', 't1083 fixture').returncode == 0
        head = _git(root, 'rev-parse', 'HEAD').stdout.strip()
        assert re.fullmatch(r'[0-9a-f]{40}', head), head
        stamp = sr.reconcile_finding(SIG_SYNC, root)
        assert stamp['reconciled_against']['commit'] == head, stamp


def test_stamps_a_loud_fallback_when_not_a_repo():
    # A stamp that vanishes on a git failure is the defect this replaces: the row is still
    # written, and it says out loud that it has no commit.
    with tempfile.TemporaryDirectory() as td:
        stamp = sr.reconcile_finding(SIG_SYNC, Path(td))
        assert stamp['reconciled_against']['commit'] == 'NO-COMMIT', stamp
        assert isinstance(stamp['reconciled_against']['checked_at'], str)
        assert stamp['reconciled_against']['checked_at'].strip(), stamp
        assert isinstance(stamp['reconciled_against']['hits'], list)
        assert stamp['resolved_in_tree'] is True, stamp


def test_violations_require_the_stamp_on_an_unresolved_row():
    # The real row shape, read off findings/done/*.json.
    row = {'kind': 'unresolved', 'disposition': 'unresolved', 'covered_by': '',
           'why_missed': 'Fix fragile replace logic in line 99'}
    v = sr.reconcile_violations(row)
    assert v, v
    assert 'resolved_in_tree' in ' '.join(v), v
    assert 'reconciled_against' in ' '.join(v), v


def test_violations_accept_a_fully_reconciled_row():
    row = {'kind': 'unresolved', 'disposition': 'unresolved', 'resolved_in_tree': False,
           'reconciled_against': {'signature': SIG_SYNC, 'commit': 'a' * 40,
                                  'checked_at': '2026-10-08T01:46:34+00:00',
                                  'hits': [{'file': 'pkg/probe.py', 'line': 99}]}}
    assert sr.reconcile_violations(row) == [], sr.reconcile_violations(row)


def test_violations_do_not_flag_a_row_the_writer_could_not_sign():
    # The writer's honest no-signature stamp must stay legal, or a finding the writer cannot sign
    # becomes a finding the writer cannot file, and the pool shrinks in silence.
    row = {'kind': 'unresolved', 'disposition': 'unresolved', 'resolved_in_tree': False,
           'reconciled_against': {'signature': '', 'commit': 'NO-COMMIT',
                                  'checked_at': '2026-10-08T01:46:34+00:00', 'hits': [],
                                  'note': 'NO-SIGNATURE'}}
    assert sr.reconcile_violations(row) == [], sr.reconcile_violations(row)


def test_violations_ignore_a_row_that_is_not_open():
    # NEGATIVE: a validator that flags every row is as useless as one that flags none.
    assert sr.reconcile_violations({'kind': 'refuted', 'disposition': 'refuted'}) == []
    assert sr.reconcile_violations({'kind': 'deferred', 'disposition': 'deferred'}) == []


def test_violations_refuse_a_resolved_defect_still_marked_unresolved():
    row = {'kind': 'unresolved', 'disposition': 'unresolved', 'resolved_in_tree': True,
           'reconciled_against': {'signature': SIG_SYNC, 'commit': 'b' * 40,
                                  'checked_at': '2026-10-08T01:46:34+00:00', 'hits': []}}
    v = sr.reconcile_violations(row)
    assert v, v
    assert 'unresolved' in ' '.join(v), v


def test_violations_accept_the_files_own_resolved_vocabulary():
    # resolved-in-session is the value in DISPOSITIONS; this file has no value named resolved.
    row = {'kind': 'resolved-in-session', 'disposition': 'resolved-in-session',
           'resolved_in_tree': True,
           'reconciled_against': {'signature': SIG_SYNC, 'commit': 'b' * 40,
                                  'checked_at': '2026-10-08T01:46:34+00:00', 'hits': []}}
    assert sr.reconcile_violations(row) == [], sr.reconcile_violations(row)


def test_violations_reject_a_line_number_as_the_signature():
    # The RCA's own defect: the locator was a line number, which moved and then was deleted.
    row = {'kind': 'unresolved', 'disposition': 'unresolved', 'resolved_in_tree': True,
           'reconciled_against': {'signature': 'line 99', 'commit': 'b' * 40,
                                  'checked_at': '2026-10-08T01:46:34+00:00', 'hits': []}}
    v = sr.reconcile_violations(row)
    assert v, v
    assert 'signature' in ' '.join(v), v


def test_violations_reject_a_stamp_with_no_commit():
    row = {'kind': 'unresolved', 'disposition': 'unresolved', 'resolved_in_tree': True,
           'reconciled_against': {'signature': SIG_SYNC,
                                  'checked_at': '2026-10-08T01:46:34+00:00', 'hits': []}}
    v = sr.reconcile_violations(row)
    assert v, v
    assert 'commit' in ' '.join(v), v


def test_violations_read_the_disposition_field_as_the_open_state():
    # A real finding row carries BOTH kind and disposition (verified on disk); the open state can
    # arrive in either, so a validator that reads only kind misses half the rows.
    v = sr.reconcile_violations({'disposition': 'unresolved', 'covered_by': ''})
    assert v, v
    assert 'resolved_in_tree' in ' '.join(v), v


def test_enters_open_pool_is_decided_by_the_stamp():
    assert sr.enters_open_pool({'kind': 'unresolved', 'resolved_in_tree': True,
                                'reconciled_against': {'signature': SIG_SYNC}}) is False
    assert sr.enters_open_pool({'kind': 'unresolved', 'resolved_in_tree': False,
                                'reconciled_against': {'signature': SIG_SYNC}}) is True
    assert sr.enters_open_pool({'kind': 'unresolved'}) is True
    assert sr.enters_open_pool({}) is True


def _main():
    tests = sorted((n, f) for n, f in list(globals().items())
                   if n.startswith('test_') and callable(f))
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('PASS %s' % name)
        except Exception as e:      # noqa: BLE001 -- a failure is the finding; report it whole
            failed += 1
            print('FAIL %s: %s: %s' % (name, type(e).__name__, e))
    print('%d test(s), %d failure(s)' % (len(tests), failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_main())
