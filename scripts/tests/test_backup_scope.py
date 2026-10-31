#!/usr/bin/env python3
'''t740: the backup must RECORD what it covers, not just that it ran.

Red on main for ONE reason: scripts/backup_corpora.py writes a MANIFEST.sha256 and a
BACKUP-INFO.txt per bundle (lines 258-275) and reports a copy's file/byte counts, but it
records no SCOPE row, so the claim 'the irreplaceable things are already covered' cannot be
confirmed, refuted, or handed over. The names this suite calls -- SCOPE_LOG,
SCOPE_MAX_AGE_HOURS, EXCLUDED_ROOTS, scope_roots, write_scope_row, read_scope_rows,
unverified_scope_rows -- do not exist in that file today, and test_the_hook_names_exist
fails alone to say so.

Run: python3 scripts/tests/test_backup_scope.py   (pytest is not installed)
No live state: rows are written only into tempfile.mkdtemp directories, nothing under
workspace/run-logs/ is written, and no subprocess, network, model or wall-clock-dependent
assertion is used -- every age test passes an explicit `now`.
'''
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_corpora as B

NOW = datetime(2026, 10, 11, 7, 0, 0, tzinfo=timezone.utc)
HOME = str(Path.home())
BUNDLE_A = '/media/usb/ai-workspace-corpora-2026-10-11'
BUNDLE_09 = '/media/usb/ai-workspace-corpora-2026-10-09'
BUNDLE_10 = '/media/usb/ai-workspace-corpora-2026-10-10'
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
    '''A scope row in the shape write_scope_row must emit: distinct real values.'''
    d = {'ts': ts(hours=-1), 'bundle': BUNDLE_A,
         'scope_roots': list(B.scope_roots()), 'excluded_roots': list(B.EXCLUDED_ROOTS),
         'files': 478, 'bytes': 170393600, 'verified': True,
         'verification_cmd': 'python3 scripts/backup_corpora.py --verify <bundle>'}
    d.update(over)
    return d


def test_the_hook_names_exist():
    names = ('SCOPE_LOG', 'SCOPE_MAX_AGE_HOURS', 'EXCLUDED_ROOTS', 'scope_roots',
             'write_scope_row', 'read_scope_rows', 'unverified_scope_rows')
    missing = [n for n in names if not hasattr(B, n)]
    if missing:
        check('scripts/backup_corpora.py defines NONE of: %s -- the scope recorder does not exist'
              % ', '.join(missing), False)
    else:
        check('scripts/backup_corpora.py defines all seven scope names', True)


def test_scope_roots_are_the_real_backup_set():
    declared = B.scope_roots()
    check('scope_roots() returns a list of strings',
          isinstance(declared, list) and all(isinstance(r, str) for r in declared))
    want = [rel for rel, _why in B.CORPORA] + [name for _src, name, _why in B.EXTERNAL]
    check('scope_roots() is COMPUTED from the file own CORPORA + EXTERNAL lists, in order',
          declared == want)
    check('the repo-relative corpora are named: chat, library',
          'chat' in declared and 'library' in declared)
    check('the two out-of-repo stores are named by archive name',
          'claude-code-sessions' in declared and 'ledger-snapshots' in declared)


def test_the_home_directory_is_recorded_as_not_covered():
    check('EXCLUDED_ROOTS names the home directory %s -- the root the deferral claimed was covered' % HOME,
          HOME in list(B.EXCLUDED_ROOTS))
    check('a root is never both covered and excluded',
          not set(B.scope_roots()) & set(B.EXCLUDED_ROOTS))


def test_write_scope_row_emits_every_required_field():
    out = Path(tempfile.mkdtemp(prefix='t740-')) / 'backup-scope.jsonl'
    written = B.write_scope_row(bundle=BUNDLE_A, files=478, bytes=170393600, verified=True,
                                ts='2026-10-11T07:51:11Z', out=out)
    check('the row is written to the path the caller names', out.is_file())
    raw = [l for l in out.read_text(encoding='utf-8').splitlines() if l.strip()]
    check('exactly one row is written by one run', len(raw) == 1)
    rec = json.loads(raw[0]) if raw else {}
    need = {'ts', 'bundle', 'scope_roots', 'excluded_roots', 'files', 'bytes', 'verified',
            'verification_cmd'}
    check('the row carries every field the task names: %s' % sorted(need), need <= set(rec))
    check('the row names the bundle it describes, verbatim', rec.get('bundle') == BUNDLE_A)
    check('the row carries the counts this run reported: 478 files, 170393600 bytes',
          rec.get('files') == 478 and rec.get('bytes') == 170393600)
    check('the row records the verification state as the bool True', rec.get('verified') is True)
    check('the row scope_roots is scope_roots() itself, not a hand-typed subset',
          rec.get('scope_roots') == B.scope_roots())
    check('the row records the home directory as NOT covered',
          HOME in list(rec.get('excluded_roots') or []))
    check('the row names the command that verifies it',
          'backup_corpora.py' in str(rec.get('verification_cmd'))
          and '--verify' in str(rec.get('verification_cmd')))
    check('the value returned is the row that was written',
          isinstance(written, dict) and written.get('files') == 478)


def test_a_row_written_without_ts_or_command_is_still_actionable():
    out = Path(tempfile.mkdtemp(prefix='t740-')) / 'backup-scope.jsonl'
    rec = B.write_scope_row(bundle='/media/usb/ai-workspace-corpora-2026-10-12', files=2,
                           bytes=20, verified=False, out=out)
    parsed = None
    try:
        parsed = datetime.strptime(str(rec.get('ts')), '%Y-%m-%dT%H:%M:%SZ')
    except (ValueError, TypeError):
        pass
    check('a run that passes no ts gets a UTC ISO-8601 Z timestamp, parseable by the same format',
          parsed is not None)
    cmd = str(rec.get('verification_cmd'))
    check('a run that passes no verification_cmd records the real command: backup_corpora.py --verify',
          'backup_corpora.py' in cmd and '--verify' in cmd)
    check('verified=False is recorded as False, never defaulted to a claim of success',
          rec.get('verified') is False)


def test_rows_append_one_per_run():
    out = Path(tempfile.mkdtemp(prefix='t740-')) / 'backup-scope.jsonl'
    B.write_scope_row(bundle=BUNDLE_09, files=10, bytes=100, verified=True, ts=ts(days=-2), out=out)
    B.write_scope_row(bundle=BUNDLE_A, files=12, bytes=200, verified=True, ts=ts(hours=-1), out=out)
    rows = B.read_scope_rows(out)
    check('two runs leave two rows: the scope log is append-only, never overwritten', len(rows) == 2)
    check('each row keeps its own bundle, oldest first',
          [r.get('bundle') for r in rows] == [BUNDLE_09, BUNDLE_A])
    check('a row read back carries its own counts, not the latest run counts',
          bool(rows) and rows[0].get('files') == 10)


def test_the_reader_survives_a_torn_final_line():
    out = Path(tempfile.mkdtemp(prefix='t740-')) / 'backup-scope.jsonl'
    good = json.dumps(row(bundle=BUNDLE_10))
    out.write_text(good + '\n' + '{"ts": "2026-10-11T07:51:11Z", "bun', encoding='utf-8')
    rows = B.read_scope_rows(out)
    check('a torn final line does not destroy the intact row before it', len(rows) == 1)
    check('the intact row is the one returned',
          bool(rows) and rows[0].get('bundle') == BUNDLE_10)


def test_a_declared_root_with_no_row_is_reported():
    declared = B.scope_roots()
    got = B.unverified_scope_rows(declared, [], now=NOW)
    check('no scope rows at all reports EVERY declared root, in declared order',
          sorted(got) == sorted(declared))


def test_a_fresh_verified_row_clears_every_root():
    out = Path(tempfile.mkdtemp(prefix='t740-')) / 'backup-scope.jsonl'
    B.write_scope_row(bundle=BUNDLE_A, files=10, bytes=100, verified=True, ts=ts(hours=-1), out=out)
    rows = B.read_scope_rows(out)
    check('a verified row written by the writer and read back by the reader clears every declared root',
          B.unverified_scope_rows(B.scope_roots(), rows, now=NOW) == [])


def test_a_verified_row_older_than_the_window_stops_counting():
    declared = B.scope_roots()
    check('the decay window is 36h, as the task states', B.SCOPE_MAX_AGE_HOURS == 36)
    check('a verified row 37h old is NOT coverage any more',
          sorted(B.unverified_scope_rows(declared, [row(ts=ts(hours=-37))], now=NOW)) == sorted(declared))
    check('the same row at 35h IS coverage: the window is honoured, not guessed',
          B.unverified_scope_rows(declared, [row(ts=ts(hours=-35))], now=NOW) == [])


def test_verified_false_never_counts_as_coverage():
    declared = B.scope_roots()
    check('a row with verified=False is NOT coverage: the claim requires the verify to have run',
          sorted(B.unverified_scope_rows(declared, [row(verified=False)], now=NOW)) == sorted(declared))


def test_an_unparseable_timestamp_is_not_silently_treated_as_fresh():
    declared = B.scope_roots()
    check('a row whose ts cannot be parsed reports every declared root rather than passing',
          sorted(B.unverified_scope_rows(declared, [row(ts='yesterday')], now=NOW)) == sorted(declared))


def test_partial_coverage_names_only_the_root_that_is_missing():
    declared = B.scope_roots()
    r = row()
    r['scope_roots'] = [x for x in declared if x != 'chat']
    got = B.unverified_scope_rows(declared, [r], now=NOW)
    check('a verified row that omits one root reports exactly that root: chat', got == ['chat'])
    check('a root the row does cover is not reported', 'library' not in got)


def test_a_row_claiming_a_root_the_code_does_not_back_up_changes_nothing():
    declared = B.scope_roots()
    r = row()
    r['scope_roots'] = list(declared) + ['/home/user']
    check('an extra root inside a row does not make a declared root look unverified',
          B.unverified_scope_rows(declared, [r], now=NOW) == [])


def test_the_default_scope_log_is_the_documented_path_under_the_repo():
    p = Path(B.SCOPE_LOG)
    check('SCOPE_LOG is workspace/run-logs/backup-scope.jsonl under the repo',
          p.name == 'backup-scope.jsonl' and p.parent.name == 'run-logs'
          and str(p).startswith(str(B.REPO_ROOT)))
    check('its directory exists, so a run can append to it', p.parent.is_dir())


def run():
    test_the_hook_names_exist()
    for name in sorted(n for n in list(globals()) if n.startswith('test_') and n != 'test_the_hook_names_exist'):
        if hasattr(B, 'scope_roots') and hasattr(B, 'write_scope_row') \
                and hasattr(B, 'read_scope_rows') and hasattr(B, 'unverified_scope_rows') \
                and hasattr(B, 'EXCLUDED_ROOTS') and hasattr(B, 'SCOPE_MAX_AGE_HOURS'):
            globals()[name]()
        else:
            check('%s needs the scope recorder (absent today)' % name, False)
    print('')
    print('%d passed, %d failed' % (ok, fail))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(run())
