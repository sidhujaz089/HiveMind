#!/usr/bin/env python3
'''t730 oracle: backup FRESHNESS, not backup existence -- scripts/error_scan.py.

WHY. scripts/check_workspace.py reads workspace/backup-runs.jsonl and calls the backup healthy
when the newest nas-backup row says verified (the clause inside check_git_state, at line 296 of
that file). It never compares that stamp to the current time, so a verified row from a month
ago passes forever. That is the 2026-09-15 shape recorded in scripts/nas_backup.sh's own head
comment: the bundle backup reported verify-failed for weeks on an INTACT backup, because nothing
required a FRESH verified row to exist.

Red on main for ONE reason: scripts/error_scan.py has no backup_freshness() and no
BACKUP_FRESHNESS_MAX_AGE_HOURS. Every case prints that reason; nothing here raises.

Why error_scan.py and not a new module: it already reads this exact ledger (DEFAULT_SOURCES), it
already owns the vocabulary the task needs -- HEALTHY_STATUS enumerates the approved statuses and
its docstring is the standing rule to enumerate the good -- and coder.py can only edit a file
that exists, so a new scripts/backup_freshness.py is unbuildable.

Offline: tempfile dirs only, no writes outside them, no model, no subprocess, no network, no
process left running.
'''
import datetime
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import error_scan as M
    IMPORT_ERR = None
except Exception as exc:
    M = None
    IMPORT_ERR = 'importing scripts/error_scan.py raised %s: %s' % (type(exc).__name__, exc)

NOW = datetime.datetime(2026, 10, 11, 12, 0, 0, tzinfo=datetime.timezone.utc)
LIMIT = 36
LEDGER = 'backup-runs.jsonl'
REMEDY = 'nas_backup.sh'
VERDICTS = ('PASS', 'FAIL', 'UNREADABLE')
NO_FN = 'scripts/error_scan.py has no backup_freshness() -- the change this scope adds'

# The seven non-healthy statuses scripts/nas_backup.sh really writes (grep its row calls:
# lines 33, 39, 46, 47, 49, 50, 75, 76, 79). A test over THESE values, not over one invented
# string, is what keeps an implementation from calling every non-healthy status unenumerated
# and reading the live ledger as UNREADABLE forever: its newest nas-backup row is prune-failed.
WRITER_STATUSES = ('died', 'unreachable', 'bundle-failed', 'bundle-corrupt',
                   'copy-failed', 'prune-failed', 'verify-failed')

ok = 0
fail = 0


def check(label, cond, got=''):
    global ok, fail
    if cond:
        ok += 1
        print('  ok   %s' % label)
    else:
        fail += 1
        print('  FAIL %s%s' % (label, (' -- got ' + str(got)) if got != '' else ''))


def stamp(**kw):
    '''A real ledger stamp: %Y-%m-%dT%H:%M:%SZ, the format all 234 live rows use.'''
    return (NOW + datetime.timedelta(**kw)).strftime('%Y-%m-%dT%H:%M:%SZ')


def row(at, status='verified', job='nas-backup',
        detail='read back cold and byte-identical; 3-version prune applied'):
    return json.dumps({'at': at, 'job': job, 'status': status, 'detail': detail})


def ledger(lines, d):
    p = Path(d) / LEDGER
    p.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return p


HAS = bool(M) and hasattr(M, 'backup_freshness')


def _r(*a, **kw):
    return M.backup_freshness(*a, **kw)


def _absent():
    check('error_scan has backup_freshness()', False, NO_FN)


def test_the_entry_point_and_the_limit_exist():
    if IMPORT_ERR:
        check('error_scan imports cleanly', False, IMPORT_ERR)
    check('error_scan has backup_freshness()', HAS, NO_FN)
    check('the limit is the 36 hours the task names',
          HAS and getattr(M, 'BACKUP_FRESHNESS_MAX_AGE_HOURS', None) == LIMIT,
          NO_FN if not HAS else getattr(M, 'BACKUP_FRESHNESS_MAX_AGE_HOURS', 'ABSENT'))


def test_a_fresh_verified_row_passes_and_says_what_it_examined():
    if not HAS:
        _absent()
        return
    with tempfile.TemporaryDirectory() as d:
        p = ledger([row(at=stamp(hours=-100)), row(at=stamp(hours=-2))], d)
        r = _r(p, now=NOW)
    check('a verified row 2h old gives verdict PASS', r.get('verdict') == 'PASS', r)
    check('it counted BOTH nas-backup rows in the population', r.get('n_rows') == 2,
          r.get('n_rows'))
    check('it reports the newest verified stamp verbatim, not a re-formatted one',
          r.get('newest_verified') == stamp(hours=-2), r.get('newest_verified'))
    det = r.get('detail', '')
    check('the detail is one non-empty record, not a fragment',
          isinstance(det, str) and len(det) >= 40, repr(det))
    check('the detail prints the population examined (2 nas-backup row(s) examined)',
          '2 nas-backup row(s) examined' in det, repr(det))
    check('the detail carries the stamp a person acts on, whole',
          stamp(hours=-2) in det, repr(det))
    check('NEGATIVE: a PASS record carries no alarm -- the remedy command is absent',
          REMEDY not in det, repr(det))


def test_a_stale_verified_row_fails_and_names_the_age():
    if not HAS:
        _absent()
        return
    with tempfile.TemporaryDirectory() as d:
        p = ledger([row(at=stamp(hours=-40))], d)
        r = _r(p, now=NOW)
    check('a verified row 40h old gives verdict FAIL', r.get('verdict') == 'FAIL', r)
    check('the age is COMPUTED from the stamp and now, not hard-coded',
          abs((r.get('age_hours') if isinstance(r.get('age_hours'), (int, float))
               else -1) - 40.0) < 0.01, r.get('age_hours'))
    det = r.get('detail', '')
    check('the detail names the stale stamp (2026-10-11T02:00:00Z)',
          stamp(hours=-40) in det, repr(det))
    check('the detail names the limit it was judged against (36)', '36' in det, repr(det))
    check('the detail names the command that clears it', REMEDY in det, repr(det))


def test_the_limit_is_older_than_so_the_boundary_itself_passes():
    if not HAS:
        _absent()
        return
    with tempfile.TemporaryDirectory() as d:
        exact = ledger([row(at=stamp(hours=-LIMIT))], d)
        over = ledger([row(at=stamp(hours=-LIMIT, seconds=-1))], d)
        a = _r(exact, now=NOW)
        b = _r(over, now=NOW)
    check('exactly 36h is NOT older than 36h, so PASS', a.get('verdict') == 'PASS', a)
    check('36h plus one second IS older, so FAIL', b.get('verdict') == 'FAIL', b)


def test_an_absent_ledger_is_unreadable_not_a_silent_pass():
    if not HAS:
        _absent()
        return
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / LEDGER
        r = _r(p, now=NOW)
    check('an absent ledger gives UNREADABLE', r.get('verdict') == 'UNREADABLE', r)
    check('UNREADABLE is never reported as a pass', r.get('verdict') != 'PASS', r.get('verdict'))
    check('the detail names the path that could not be read', str(p) in r.get('detail', ''),
          repr(r.get('detail', '')))


def test_a_torn_line_is_unreadable_not_a_silent_pass():
    if not HAS:
        _absent()
        return
    with tempfile.TemporaryDirectory() as d:
        p = ledger([row(at=stamp(hours=-2)), '{ "at": "2026-10-1'], d)
        r = _r(p, now=NOW)
    check('a torn final line gives UNREADABLE', r.get('verdict') == 'UNREADABLE', r)
    check('the torn line is COUNTED, not skipped', r.get('n_unreadable') == 1,
          r.get('n_unreadable'))
    det = r.get('detail', '')
    check('the detail says why: unparseable', 'unparseable' in det.lower(), repr(det))
    check('the detail gives the 1-based line number (line 2)', 'line 2' in det, repr(det))


def test_a_status_the_writer_never_emits_is_unreadable():
    if not HAS:
        _absent()
        return
    with tempfile.TemporaryDirectory() as d:
        p = ledger([row(at=stamp(hours=-2), status='witnessed')], d)
        r = _r(p, now=NOW)
    check('an unenumerated status gives UNREADABLE, never a pass',
          r.get('verdict') == 'UNREADABLE', r)
    check('the detail NAMES the unenumerated status', 'witnessed' in r.get('detail', ''),
          repr(r.get('detail', '')))


def test_the_statuses_the_writer_actually_emits_are_findings_not_an_unreadable_ledger():
    if not HAS:
        _absent()
        return
    for s in WRITER_STATUSES:
        with tempfile.TemporaryDirectory() as d:
            p = ledger([row(at=stamp(hours=-2)), row(at=stamp(hours=-1), status=s)], d)
            r = _r(p, now=NOW)
        check('a real %s row does NOT make the ledger unreadable' % s,
              r.get('verdict') == 'PASS', r)
        check('a real %s row is counted in the population (n_rows 2)' % s,
              r.get('n_rows') == 2, r.get('n_rows'))
        check('a real %s row is not counted as a verified row' % s,
              r.get('newest_verified') == stamp(hours=-2), r.get('newest_verified'))


def test_rows_from_the_other_two_jobs_are_not_the_population():
    if not HAS:
        _absent()
        return
    with tempfile.TemporaryDirectory() as d:
        p = ledger([row(at=stamp(hours=-2), job='night-queue'),
                    row(at=stamp(hours=-2), job='collect-logs')], d)
        r = _r(p, now=NOW)
    check('a fresh verified night-queue row is NOT a backup', r.get('verdict') == 'FAIL', r)
    check('the population is the nas-backup rows only (n_rows 0)', r.get('n_rows') == 0,
          r.get('n_rows'))
    check('and it says so: 0 nas-backup row(s) examined',
          '0 nas-backup row(s) examined' in r.get('detail', ''), repr(r.get('detail', '')))


def test_no_verified_row_at_all_is_a_fail_not_a_pass():
    if not HAS:
        _absent()
        return
    with tempfile.TemporaryDirectory() as d:
        p = ledger([row(at=stamp(hours=-2), status='verify-failed'),
                    row(at=stamp(hours=-1), status='died')], d)
        r = _r(p, now=NOW)
    check('nas-backup rows that never verified give FAIL', r.get('verdict') == 'FAIL', r)
    check('newest_verified is None, not a stale stamp', r.get('newest_verified') is None,
          r.get('newest_verified'))
    check('the record still names the way out', REMEDY in r.get('detail', ''),
          repr(r.get('detail', '')))


def test_an_unparseable_stamp_on_a_verified_row_is_unreadable():
    if not HAS:
        _absent()
        return
    with tempfile.TemporaryDirectory() as d:
        p = ledger([row(at='yesterday', status='verified')], d)
        r = _r(p, now=NOW)
    check('a verified row with an unparseable stamp gives UNREADABLE',
          r.get('verdict') == 'UNREADABLE', r)
    check('the detail names the stamp it could not parse',
          'yesterday' in r.get('detail', ''), repr(r.get('detail', '')))


def test_the_newest_verified_stamp_wins_over_a_later_failure():
    if not HAS:
        _absent()
        return
    with tempfile.TemporaryDirectory() as d:
        p = ledger([row(at=stamp(hours=-20)), row(at=stamp(hours=-1), status='prune-failed')], d)
        r = _r(p, now=NOW)
    check('newest_verified is the verified row at -20h, not the newer failed row at -1h',
          r.get('newest_verified') == stamp(hours=-20), r.get('newest_verified'))
    check('NEGATIVE: the later non-verified stamp is not reported as the verified one',
          stamp(hours=-1) != r.get('newest_verified'), r.get('newest_verified'))
    check('its age is measured from -20h, i.e. 20',
          abs((r.get('age_hours') if isinstance(r.get('age_hours'), (int, float))
               else -1) - 20.0) < 0.01, r.get('age_hours'))


def test_the_verdict_vocabulary_is_the_three_the_task_names():
    if not HAS:
        _absent()
        return
    with tempfile.TemporaryDirectory() as d:
        fresh = ledger([row(at=stamp(hours=-2))], d)
        stale = ledger([row(at=stamp(hours=-40))], d)
        torn = ledger([row(at=stamp(hours=-2)), 'not json'], d)
        got = [_r(fresh, now=NOW).get('verdict'), _r(stale, now=NOW).get('verdict'),
               _r(torn, now=NOW).get('verdict')]
    check('every verdict is one of PASS, FAIL, UNREADABLE -- spelled exactly',
          all(v in VERDICTS for v in got), got)
    check('the three fixtures produced three DIFFERENT verdicts',
          len(set(got)) == 3, got)


def test_the_default_now_is_the_real_clock():
    if not HAS:
        _absent()
        return
    live = (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
    with tempfile.TemporaryDirectory() as d:
        p = ledger([row(at=live)], d)
        r = _r(p)
    check('with no now passed, a 2h-old verified row PASSES: the default is the live clock',
          r.get('verdict') == 'PASS', r)


def _report_live_vocabulary():
    '''Read-only, informational. NO assertion depends on this: it exists so the builder can see
    the vocabulary it must not invent -- the jobs and statuses the real ledger holds.'''
    try:
        p = Path(__file__).resolve().parents[2] / 'workspace' / LEDGER
        jobs = {}
        newest = None
        for line in p.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            jobs.setdefault(rec.get('job'), set()).add(rec.get('status'))
            if rec.get('job') == 'nas-backup' and rec.get('status') == 'verified':
                if newest is None or rec.get('at') > newest:
                    newest = rec.get('at')
        print('  info live ledger: jobs/statuses %s' % {k: sorted(v) for k, v in sorted(jobs.items())})
        print('  info newest verified nas-backup row: %s (36h window is the test)' % newest)
    except Exception as exc:
        print('  info live ledger not readable (%s: %s) -- no assertion depends on it' %
              (type(exc).__name__, exc))


TESTS = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]


def gate(t):
    global fail
    try:
        t()
    except Exception as exc:
        fail += 1
        print('  FAIL %s raised %s: %s -- an oracle reports, it does not crash'
              % (t.__name__, type(exc).__name__, exc))


if __name__ == '__main__':
    print('t730 backup freshness: %d test(s) over scripts/error_scan.py' % len(TESTS))
    for t in TESTS:
        print(t.__name__)
        gate(t)
    _report_live_vocabulary()
    print('')
    print('%d passed, %d failed' % (ok, fail))
    sys.exit(1 if fail else 0)
