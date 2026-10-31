#!/usr/bin/env python3
'''t740: the workspace check must FAIL on a scope the backup has not verified.

Red on main for ONE reason: scripts/check_workspace.py has no backup_scope_findings(), so
the backup check in this file has no way to read the backup-scope rows. Today that check
gates the RECENCY of a verified row (t730) and says nothing about SCOPE, which is the whole
substance of workspace/rca/ecee0de5e0ab.md DE-2.

Run: python3 scripts/tests/test_check_workspace_backup_scope.py (pytest is not installed)
Pure-function tests: every row is a plain dict built in this file, no live state, no
subprocess, no model, no file written, and every age test passes an explicit `now` so
nothing depends on the wall clock. If backup_scope_findings is absent, every test records a
FAIL naming it -- the suite never raises AttributeError.
'''
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_workspace as W

NOW = datetime(2026, 10, 11, 7, 0, 0, tzinfo=timezone.utc)
ROOTS = ['chat', 'library', 'workspace/attachments', 'projects/iso-assistant/ISO PDFs',
         'claude-code-sessions', 'ledger-snapshots']
BUNDLE = '/mnt/nas/Public/HiveMind/ai-workspace-corpora-2026-10-11'
HAS = hasattr(W, 'backup_scope_findings')
ok = 0
fail = 0


def check(label, cond):
    global ok, fail
    print('  [%s] %s' % ('ok' if cond else 'FAIL', label))
    if cond:
        ok += 1
    else:
        fail += 1


def ts(**kw):
    return (NOW + timedelta(**kw)).strftime('%Y-%m-%dT%H:%M:%SZ')


def row(**over):
    d = {'ts': ts(hours=-1), 'bundle': BUNDLE, 'scope_roots': list(ROOTS),
         'excluded_roots': [str(Path.home())], 'files': 478, 'bytes': 170393600,
         'verified': True,
         'verification_cmd': 'python3 scripts/backup_corpora.py --verify <bundle>'}
    d.update(over)
    return d


def call(*a, **kw):
    return W.backup_scope_findings(*a, **kw) if HAS else None


def gate():
    if HAS:
        return False
    check('scripts/check_workspace.py defines backup_scope_findings (it does not today)', False)
    return True


def test_the_hook_exists():
    check('scripts/check_workspace.py defines backup_scope_findings', HAS)


def test_no_rows_reports_every_declared_root():
    if gate():
        return
    got = call(ROOTS, [], now=NOW)
    check('an empty scope log yields one finding per declared root, naming each root',
          len(got) == len(ROOTS) and all(any(r in f for f in got) for r in ROOTS))
    check('no finding invents a root the code does not declare',
          all('network-drive' not in f for f in got))


def test_a_fresh_verified_row_clears_every_root():
    if gate():
        return
    got = call(ROOTS, [row()], now=NOW)
    check('one verified row 1h old covering all six roots produces NO finding', got == [])


def test_a_verified_row_older_than_the_window_fails_again():
    if gate():
        return
    check('a verified row 37h old is not coverage: every declared root is reported',
          sorted(call(ROOTS, [row(ts=ts(hours=-37))], now=NOW)) == sorted(ROOTS))
    check('the same row at 35h IS coverage: the 36h window is honoured',
          call(ROOTS, [row(ts=ts(hours=-35))], now=NOW) == [])


def test_the_window_argument_is_honoured_not_hardcoded():
    if gate():
        return
    check('max_age_hours=24 makes a 30h-old verified row stale',
          sorted(call(ROOTS, [row(ts=ts(hours=-30))], max_age_hours=24, now=NOW)) == sorted(ROOTS))


def test_a_verified_false_row_is_not_coverage():
    if gate():
        return
    check('verified=False is not coverage: every declared root is reported',
          sorted(call(ROOTS, [row(verified=False)], now=NOW)) == sorted(ROOTS))
    check('a row with no verified key at all is not coverage',
          sorted(call(ROOTS, [row(verified=None)], now=NOW)) == sorted(ROOTS))


def test_a_partial_row_names_only_the_root_that_is_missing():
    if gate():
        return
    r = row()
    r['scope_roots'] = [x for x in ROOTS if x != 'chat']
    got = call(ROOTS, [r], now=NOW)
    check('a verified row that omits one root reports exactly one finding, naming chat',
          len(got) == 1 and 'chat' in got[0])
    check('the five roots the row does cover are NOT reported',
          not any('library' in f for f in got))


def test_a_row_with_an_empty_scope_roots_list_fails_every_root():
    if gate():
        return
    check('a verified row covering nothing is a total coverage gap',
          sorted(call(ROOTS, [row(scope_roots=[])], now=NOW)) == sorted(ROOTS))


def test_an_unparseable_timestamp_is_reported_not_treated_as_fresh():
    if gate():
        return
    got = call(ROOTS, [row(ts='yesterday')], now=NOW)
    check('a row whose ts cannot be parsed reports every declared root',
          sorted(got) == sorted(ROOTS))
    check('  and the finding names the bundle whose row could not be read',
          bool(got) and all(BUNDLE in f for f in got))


def test_a_row_claiming_an_undeclared_root_is_a_finding():
    if gate():
        return
    r = row()
    r['scope_roots'] = list(ROOTS) + ['/home/user']
    got = call(ROOTS, [r], now=NOW)
    check('a row claiming a root the code does not back up is reported as drift',
          any('/home/user' in f for f in got))
    check('  and it does not make a declared root look unverified',
          not any('chat' in f for f in got))


def test_every_finding_says_where_to_look_and_what_to_run():
    if gate():
        return
    got = call(ROOTS, [], now=NOW)
    check('every finding names the log the reader must read: backup-scope.jsonl',
          all('backup-scope.jsonl' in f for f in got))
    check('every finding names the command that clears it: backup_corpora.py --verify',
          all('backup_corpora.py' in f and '--verify' in f for f in got))


if __name__ == '__main__':
    test_the_hook_exists()
    test_no_rows_reports_every_declared_root()
    test_a_fresh_verified_row_clears_every_root()
    test_a_verified_row_older_than_the_window_fails_again()
    test_the_window_argument_is_honoured_not_hardcoded()
    test_a_verified_false_row_is_not_coverage()
    test_a_partial_row_names_only_the_root_that_is_missing()
    test_a_row_with_an_empty_scope_roots_list_fails_every_root()
    test_an_unparseable_timestamp_is_reported_not_treated_as_fresh()
    test_a_row_claiming_an_undeclared_root_is_a_finding()
    test_every_finding_says_where_to_look_and_what_to_run()
    print('')
    print('%d passed, %d failed' % (ok, fail))
    sys.exit(1 if fail else 0)
