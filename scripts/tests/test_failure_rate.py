#!/usr/bin/env python3
'''t737: scripts/failure_rate.py --kind <class> counts findings per day.

Red on main for exactly one reason: scripts/failure_rate.py does not exist
(ls -l scripts/failure_rate.py -> No such file or directory, 2026-10-11). There is no
script to run, so every check below fails for that reason and for no other -- not a
syntax error in this file, not a pytest import, not a repository root one level short,
not a signature this brief does not change.

What a person counts by hand, and what this asserts: the NUMBER of deferral findings on
a given DAY. The fixtures use the field names the real corpus has, both verified
2026-10-11 by reading the files:
  workspace/session-reviews/findings/*.json  247 files: 161 deferral, 62 unresolved,
      24 off-flow. The class lives under the key "kind"; the timestamp under "found".
  workspace/session-reviews/ledger.jsonl     1199 rows: 299 deferral, 119 unresolved,
      63 off-flow, 718 rows with no kind key. The class lives under "kind"; the
      timestamp under "at".
So a row counts toward the asked kind when it has a key named kind whose value equals
the asked kind, and its day is the FIRST string value in the row matching a YYYY-MM-DD
prefix. The rule names no key for the date, because the two corpora spell the same fact
differently.

  - exact day -> count pairs, so a stub returning a constant, {} or a bare total fails;
  - a non-deferral row on the same day that must NOT be counted -- the negative that
    makes "count every row" fail;
  - a kind no fixture row carries must total 0, not everything;
  - a truncated JSON file that must be NAMED in the output, not skipped in silence;
  - a findings directory that does not exist must exit non-zero and name the path, so a
    collector that prints all-zero counts for a path it never opened fails;
  - the real corpus, read from a directory that is not the repo root, where deferrals
    must outnumber unresolved and off-flow findings (measured 161 / 62 / 24).

Run: python3 scripts/tests/test_failure_rate.py   (pytest is not installed)
'''
import json
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE = ROOT / 'scripts' / 'failure_rate.py'
REAL_FINDINGS = ROOT / 'workspace' / 'session-reviews' / 'findings'

DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')

failures = []


def check(name, cond, detail=''):
    if cond:
        print('ok   - ' + name)
    else:
        print('FAIL - ' + name + '  ' + str(detail))
        failures.append(name)


def run_cli(args, cwd=None, timeout=120):
    return subprocess.run([sys.executable, str(MODULE)] + list(args),
                          capture_output=True, text=True, timeout=timeout,
                          cwd=str(cwd if cwd is not None else ROOT))


def payload(cp):
    '''The single JSON object the CLI prints, or None if it printed none.'''
    try:
        obj = json.loads(cp.stdout)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def make_findings(d):
    '''4 files: 2 deferrals on 2026-10-01, 1 deferral on 2026-10-06, and 1
    unresolved on 2026-10-01 that must NOT be counted. Real field names: kind, found.'''
    d = pathlib.Path(d)
    (d / 'a.json').write_text(json.dumps({
        'key': 'a1', 'kind': 'deferral', 'found': '2026-10-01T14:50:33Z',
        'quote': 'Say if you want it gone too.', 'covered_by': ''}), encoding='utf-8')
    (d / 'b.json').write_text(json.dumps({
        'key': 'b2', 'kind': 'deferral', 'found': '2026-10-01T19:34:42Z',
        'quote': 'Should I remove Nemotron too?', 'covered_by': ''}), encoding='utf-8')
    (d / 'c.json').write_text(json.dumps({
        'key': 'c3', 'kind': 'deferral', 'found': '2026-10-06T08:00:00Z',
        'quote': '14 branches unmerged.', 'covered_by': ''}), encoding='utf-8')
    (d / 'd.json').write_text(json.dumps({
        'key': 'd4', 'kind': 'unresolved', 'found': '2026-10-01T09:00:00Z',
        'quote': 'The command checker is still unavailable.',
        'covered_by': ''}), encoding='utf-8')


def make_ledger(path):
    '''Real ledger field names: kind, at. One row is NOT a deferral and must not count.'''
    rows = [{'kind': 'deferral', 'at': '2026-10-06T09:00:00Z', 'key': 'aaa111'},
            {'kind': 'deferral', 'at': '2026-10-07T12:00:00Z', 'key': 'bbb222'},
            {'kind': 'unresolved', 'at': '2026-10-07T13:00:00Z', 'key': 'ccc333'}]
    pathlib.Path(path).write_text(
        ''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')


def test_0_module_exists():
    check('scripts/failure_rate.py exists', MODULE.is_file(),
          'expected a file at ' + str(MODULE))


def test_1_counts_deferrals_per_day():
    with tempfile.TemporaryDirectory() as d:
        make_findings(d)
        cp = run_cli(['--kind', 'deferral', '--findings', d])
        check('exit 0 on a findings dir', cp.returncode == 0,
              'rc=%s out=%r err=%r' % (cp.returncode, cp.stdout[:200], cp.stderr[:200]))
        obj = payload(cp)
        if obj is None:
            check('prints one JSON object', False,
                  'stdout=%r err=%r' % (cp.stdout[:200], cp.stderr[:200]))
            return
        check('per_day is a dict of day -> int',
              isinstance(obj.get('per_day'), dict), 'keys=' + str(sorted(obj)))
        # THE NUMBER A PERSON COUNTS BY HAND: 2 on 2026-10-01, 1 on 2026-10-06.
        check("per_day == {'2026-10-01': 2, '2026-10-06': 1}",
              obj.get('per_day') == {'2026-10-01': 2, '2026-10-06': 1},
              'got ' + repr(obj.get('per_day')))
        check('total is the sum of the days', obj.get('total') == 3,
              'got ' + repr(obj.get('total')))
        check('the kind echoed back is the kind asked for',
              obj.get('kind') == 'deferral', 'got ' + repr(obj.get('kind')))
        # NEGATIVE: the unresolved finding on 2026-10-01 must not leak in (would be 3).
        check('a non-deferral finding on 2026-10-01 is NOT counted',
              (obj.get('per_day') or {}).get('2026-10-01') == 2,
              'got ' + repr(obj.get('per_day')))
        # NEGATIVE: no fixture row is off-flow, so that kind must total 0, not 4.
        cp2 = run_cli(['--kind', 'off-flow', '--findings', d])
        obj2 = payload(cp2)
        check('a kind no finding carries totals 0',
              isinstance(obj2, dict) and obj2.get('total') == 0
              and obj2.get('per_day') == {}, 'got ' + repr(obj2))


def test_2_ledger_rows_land_on_their_day():
    with tempfile.TemporaryDirectory() as d:
        make_findings(d)
        ledger = pathlib.Path(d) / 'ledger.jsonl'
        make_ledger(ledger)
        cp = run_cli(['--kind', 'deferral', '--findings', d, '--ledger', str(ledger)])
        check('exit 0 with a ledger', cp.returncode == 0,
              'rc=%s err=%r' % (cp.returncode, cp.stderr[:200]))
        obj = payload(cp)
        if obj is None:
            check('ledger run prints JSON', False, 'stdout=%r' % cp.stdout[:200])
            return
        # findings + ledger: 2026-10-06 gains the ledger row -> 2; 2026-10-07 appears.
        check('findings + ledger == {01:2, 06:2, 07:1}',
              obj.get('per_day') == {'2026-10-01': 2, '2026-10-06': 2,
                                     '2026-10-07': 1},
              'got ' + repr(obj.get('per_day')))
        # NEGATIVE: the unresolved ledger row on 2026-10-07 must not count (would be 2).
        check('a non-deferral ledger row on 2026-10-07 is NOT counted',
              (obj.get('per_day') or {}).get('2026-10-07') == 1,
              'got ' + repr(obj.get('per_day')))


def test_3_unparseable_file_is_reported_not_swallowed():
    with tempfile.TemporaryDirectory() as d:
        make_findings(d)
        (pathlib.Path(d) / 'broken.json').write_text(
            '{"kind": "deferral", "found": ', encoding='utf-8')
        cp = run_cli(['--kind', 'deferral', '--findings', d])
        obj = payload(cp)
        if obj is None:
            check('an unparseable file still lets the CLI print its JSON', False,
                  'stdout=%r err=%r' % (cp.stdout[:200], cp.stderr[:200]))
            return
        check('the unreadable file is NAMED in the output',
              any('broken.json' in str(p) for p in (obj.get('unreadable') or [])),
              'unreadable=' + repr(obj.get('unreadable')))
        check('an unreadable file does not change the counts that can be read',
              obj.get('per_day') == {'2026-10-01': 2, '2026-10-06': 1},
              'got ' + repr(obj.get('per_day')))


def test_4_missing_findings_dir_fails_loudly():
    with tempfile.TemporaryDirectory() as d:
        missing = str(pathlib.Path(d) / 'no-such-findings')
        cp = run_cli(['--kind', 'deferral', '--findings', missing])
        check('a nonexistent findings dir exits non-zero', cp.returncode != 0,
              'rc=%s out=%r' % (cp.returncode, cp.stdout[:200]))
        check('the failure names the path it could not read',
              'no-such-findings' in (cp.stdout + cp.stderr),
              'out=%r err=%r' % (cp.stdout[:200], cp.stderr[:200]))


def test_5_real_corpus_counted_from_a_foreign_cwd():
    '''The real trend line, read read-only. Run from a temp cwd so a default resolved
    against os.getcwd() (rather than from the module file) cannot pass. Deferrals must
    outnumber the other two classes (measured 2026-10-11: 161 / 62 / 24) -- asserted as
    inequalities so the suite cannot rot when the corpus grows. A stub that returns one
    constant, counts every row, or returns {} fails at least one of these.'''
    if not REAL_FINDINGS.is_dir():
        print('SKIP - real findings dir absent: ' + str(REAL_FINDINGS))
        return
    with tempfile.TemporaryDirectory() as cwd:
        got = {}
        for kind in ('deferral', 'unresolved', 'off-flow'):
            cp = run_cli(['--kind', kind], cwd=cwd)
            obj = payload(cp)
            if obj is None:
                check('real corpus: --kind %s runs on the default path' % kind, False,
                      'rc=%s out=%r err=%r' % (cp.returncode, cp.stdout[:200],
                                               cp.stderr[:200]))
                return
            check('real corpus: --kind %s exits 0 from a foreign cwd' % kind,
                  cp.returncode == 0, 'rc=%s' % cp.returncode)
            total = obj.get('total')
            per_day = obj.get('per_day')
            check('real corpus: %s total is a positive int' % kind,
                  isinstance(total, int) and total > 0, 'total=' + repr(total))
            check('real corpus: %s day keys are all ISO days' % kind,
                  isinstance(per_day, dict)
                  and all(DAY.match(str(k)) for k in per_day),
                  'keys=' + repr(sorted(per_day)[:5] if isinstance(per_day, dict) else per_day))
            check('real corpus: %s total == sum(per_day.values())' % kind,
                  isinstance(total, int) and isinstance(per_day, dict)
                  and sum(per_day.values()) == total,
                  'total=%r per_day=%r' % (total, per_day))
            got[kind] = total
        check('deferrals outnumber unresolved findings (161 vs 62 today)',
              got.get('deferral', 0) > got.get('unresolved', 0), str(got))
        check('deferrals outnumber off-flow findings (161 vs 24 today)',
              got.get('deferral', 0) > got.get('off-flow', 0), str(got))


if __name__ == '__main__':
    for fn in (test_0_module_exists, test_1_counts_deferrals_per_day,
               test_2_ledger_rows_land_on_their_day,
               test_3_unparseable_file_is_reported_not_swallowed,
               test_4_missing_findings_dir_fails_loudly,
               test_5_real_corpus_counted_from_a_foreign_cwd):
        try:
            fn()
        except Exception as exc:
            check('%s raised' % fn.__name__, False,
                  '%s: %s' % (type(exc).__name__, exc))
    print('')
    print('%d failure(s)' % len(failures))
    sys.exit(1 if failures else 0)
