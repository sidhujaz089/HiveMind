'''t1064: ONE dated roll-up triage task for the ownerless 'deferred' findings.

The task (workspace/tasks/t1064--file-the-121-ownerless-deferred-findings-as-one.md) asks that a
sweep finding N>10 uncovered deferrals file ONE dated roll-up triage task, at the top of the queue,
whose body lists all N keys with quote/turn/date, and stamp each finding's covered_by with that
single id; disposition 'deferred' gains an owner and an expiry on the record.

REBUILT 2026-10-14 as a NEW file. scripts/tests/test_rollup_triage_filing.py is frozen at bridge
commit 2afb4393 (the bridge will not overwrite a suite: scripts/bridge.py "already exists with
different text"), and its fixtures carried the two defects Claude's 2026-10-14 merge review
recorded. Both are pinned below as FIXTURES, not prose:

1. Its queue fixture was invented ('t900  Do the other thing  [workspace]  queued'). The real
   queue is taskstore.write_queue's numbered rows -- '1. t900 — title [area] queued' -- under a
   multi-line header, and taskstore owns that file, its lock, its ids and its numbering. Wiring an
   invented row into the real file is a foreign format inside live state.
2. It let the CALLER pass task_id, and detected "this sweep already ran today" as source
   'session-rca' + created today. Every RCA task session_review._file_task files carries exactly
   that source and date, so on any day an RCA files a task the roll-up would return that unrelated
   RCA's id, stamp every finding with it, and file nothing.

So every assertion below drives the REAL store (projects/app-repo/taskstore.py) into a temp tree
and reads back what the store wrote. The store mints the id; the caller must not supply one.

Records are the shape this repository's RCA path already reads: key, kind, quote, where, found,
with disposition and covered_by written by write_rca_outcome. Every record here carries its OWN
quote, turn number and date, so a body that repeats one placeholder for all of them fails.

No live state: each test points the real store's TASKS, DONE, QUEUE and LOCK at its own temp dir,
asserts nothing outside it, and writes no fixed /tmp path. No model call, no server, no subprocess.

Run with plain python3 (pytest is not installed):
    python3 scripts/tests/test_t1064_rollup_triage_filing.py
'''
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'projects' / 'app-repo'))

try:
    import session_review as sr
except Exception as exc:
    print('FATAL: cannot import scripts/session_review.py: %s' % exc)
    sys.exit(1)
try:
    import taskstore as ts
except Exception as exc:
    print('FATAL: cannot import projects/app-repo/taskstore.py: %s' % exc)
    sys.exit(1)

TODAY = '2026-10-12'
NEXT_DAY = '2026-10-13'
DUE = '2026-10-26'          # TODAY + 14 days
_failures = []


def check(cond, msg):
    if not cond:
        _failures.append(msg)
        print('FAIL: ' + msg)


def store(tmp):
    '''Point the REAL store at a temp tree and write a real, header-only QUEUE.md through
    taskstore.write_queue, so every queue row asserted below is taskstore's own format.
    Nothing under the repository is touched: RESOLVED.md derives from TASKS at call time.'''
    tasks = Path(tmp) / 'tasks'
    tasks.mkdir(parents=True, exist_ok=True)
    ts.TASKS = tasks
    ts.DONE = tasks / 'done'
    ts.QUEUE = tasks / 'QUEUE.md'
    ts.LOCK = tasks / '.lock'
    ts.write_queue([])
    return tasks


def finding(key, i, disposition='deferred', covered_by=''):
    return {'key': key, 'kind': 'deferral',
            'quote': 'quote number %d: a distinct sentence for record %s' % (i, key),
            'where': 'assistant, turn %d of 602' % (100 + i),
            'found': '2026-09-%02d' % (10 + i),
            'disposition': disposition,
            'covered_by': covered_by}


def uncovered(n):
    return [finding('k%02d' % i, i) for i in range(n)]


def files_in(tmp):
    return sorted(p.name for p in Path(tmp).glob('tasks/t*--*.md'))


def numbered(text):
    '''[(row, id)] for every numbered task row of a real QUEUE.md, in file order.'''
    out = []
    for line in text.splitlines():
        m = re.match(r'^\s*\d+\.\s+(t\d+)\b', line)
        if m:
            out.append((line, m.group(1)))
    return out


def row_text(row):
    return re.sub(r'^\s*\d+\.\s+', '', row)


def rca_task(tmp):
    '''A real RCA-shaped task on disk: source session-rca, created today. This is the file the
    retired oracle's dedupe matched, and the reason that dedupe was wrong.'''
    tid = ts.add('An unrelated RCA task filed the same day', area='workspace',
                 body='Findings from an RCA with nothing to do with the roll-up.',
                 source='session-rca')
    names = files_in(tmp)
    check(len(names) == 1, 'the RCA fixture writes exactly one task file, got %r' % names)
    path = Path(tmp) / 'tasks' / (names[0] if names else 'none')
    text = path.read_text(encoding='utf-8')
    text = re.sub(r'(?m)^created: .*$', 'created: 2026-10-12T051446Z', text, 1)
    path.write_text(text, encoding='utf-8')
    return tid, path


def test_the_roll_up_filer_exists():
    if not callable(getattr(sr, 'roll_up_triage', None)):
        raise AssertionError(
            'scripts/session_review.py defines no roll_up_triage; the task names it and nothing on '
            'main does (grep -rn "roll_up" --include=*.py . finds the name only inside the retired '
            'scripts/tests/test_rollup_triage_filing.py)')


def test_at_ten_uncovered_deferrals_it_files_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        store(tmp)
        empty = ts.QUEUE.read_text(encoding='utf-8')
        f = uncovered(10)
        res = sr.roll_up_triage(f, today=TODAY)
        if not isinstance(res, dict):
            raise AssertionError('the roll-up must return a dict, got %r' % (res,))
        check(res.get('filed') is False,
              'N=10 is at the threshold, not over it, so nothing is filed; got %r' % (res,))
        check(res.get('count') == 10, 'the count reported is 10, got %r' % (res.get('count'),))
        check(not files_in(tmp), 'N=10 writes no task file, wrote %r' % files_in(tmp))
        check(all(str(r.get('covered_by', '')) == '' for r in f),
              'N=10 stamps no finding, got %r' % [r.get('covered_by') for r in f])
        check(all('owner' not in r for r in f), 'N=10 gives no finding an owner')
        after = ts.QUEUE.read_text(encoding='utf-8')
        check(after == empty,
              'N=10 leaves QUEUE.md byte-identical, got %r' % after.splitlines()[-3:])


def test_at_eleven_it_files_one_task_through_the_store():
    with tempfile.TemporaryDirectory() as tmp:
        store(tmp)
        f = uncovered(11)
        res = sr.roll_up_triage(f, today=TODAY)
        check(isinstance(res, dict), 'returns a dict, got %r' % (res,))
        check(res.get('filed') is True,
              'N=11 is over the threshold, so one task is filed; got %r' % (res,))
        check(res.get('count') == 11, 'the count reported is 11, got %r' % (res.get('count'),))
        tid = str(res.get('task_id') or '')
        if not re.match(r'^t\d+$', tid):
            raise AssertionError('the id must be one the store minted, not one the caller supplied; '
                                 'taskstore.add owns _next_id, got %r' % (tid,))
        names = files_in(tmp)
        check(len(names) == 1, 'exactly ONE task file for 11 findings, got %r' % names)
        check(bool(names) and names[0].startswith(tid + '--'),
              'the file is the name the store gives the id it minted, got %r vs %r' % (names, tid))
        check(not any('t9999' in n for n in names), 'no invented id appears, got %r' % names)
        text = (Path(tmp) / 'tasks' / names[0]).read_text(encoding='utf-8') if names else ''
        if not text.startswith('---'):
            raise AssertionError('the task must be a taskstore file with front matter, got %r'
                                 % text[:60])
        for want in ('id: ' + tid, 'status: queued', 'source: session-rollup'):
            check(want in text,
                  'the front matter carries %r -- the roll-up has its own source, not the '
                  'session-rca source every RCA task carries, got %r' % (want, text[:400]))
        check('Roll-up filed by the session-review sweep of %s.' % TODAY in text,
              'the body carries the marker line that makes a second sweep the same day dedupe')
        check(TODAY in text, 'the task names the sweep date it is dated for, got %r' % text[:300])
        for i, rec in enumerate(f):
            check(rec['key'] in text, 'the body lists finding key %s' % rec['key'])
            check(rec['quote'] in text,
                  'the body carries %s verbatim (its own quote, not a shared placeholder)'
                  % rec['key'])
            check('turn %d of 602' % (100 + i) in text,
                  'the body carries the turn number for %s' % rec['key'])
            check(rec['found'] in text,
                  'the body carries the found date for %s' % rec['key'])


def test_the_stamps_land_on_the_records_themselves():
    with tempfile.TemporaryDirectory() as tmp:
        store(tmp)
        f = uncovered(11)
        res = sr.roll_up_triage(f, today=TODAY)
        tid = str(res.get('task_id'))
        for rec in f:
            check(rec.get('covered_by') == tid,
                  '%s is stamped covered_by the roll-up id, got %r'
                  % (rec['key'], rec.get('covered_by')))
            check(rec.get('owner') == 'manager',
                  '%s gains an owner on the record, got %r' % (rec['key'], rec.get('owner')))
            check(rec.get('due') == DUE,
                  '%s gains an expiry 14 days from %s, got %r' % (rec['key'], TODAY, rec.get('due')))
            check(rec.get('disposition') == 'deferred',
                  '%s keeps the disposition it came with' % rec['key'])
            check(rec.get('kind') == 'deferral', '%s keeps its kind' % rec['key'])


def test_findings_that_are_not_uncovered_deferrals_are_left_alone():
    with tempfile.TemporaryDirectory() as tmp:
        store(tmp)
        f = uncovered(11) + [finding('owned777', 40, covered_by='t777'),
                             finding('openseq41', 41, disposition='open'),
                             finding('closed888', 42, covered_by='t888')]
        res = sr.roll_up_triage(f, today=TODAY)
        check(res.get('count') == 11,
              'only uncovered deferrals are counted: 11 of %r, got %r'
              % (len(f), res.get('count')))
        names = files_in(tmp)
        text = (Path(tmp) / 'tasks' / names[0]).read_text(encoding='utf-8') if names else ''
        for rec in f[11:]:
            check(rec['key'] not in text,
                  'the roll-up body does not list %s (not an uncovered deferral)' % rec['key'])
            check('owner' not in rec,
                  'a record the roll-up did not select gains no owner, got %r' % rec)
            check('due' not in rec,
                  'a record the roll-up did not select gains no due, got %r' % rec)
        check(f[11].get('covered_by') == 't777',
              'an already-covered record keeps its own id, got %r' % f[11].get('covered_by'))
        check(f[12].get('covered_by') == '',
              'a non-deferred record keeps a blank covered_by, got %r' % f[12].get('covered_by'))
        check(f[13].get('covered_by') == 't888',
              'a deferred record that already has an owner keeps it, got %r'
              % f[13].get('covered_by'))


def test_the_queue_row_is_the_first_numbered_row_and_nothing_else_moves():
    with tempfile.TemporaryDirectory() as tmp:
        store(tmp)
        ts.add('The work Jas put first', area='workspace', source='jas')
        ts.add('A second queued task from an RCA', area='workspace', source='session-rca')
        before_raw = ts.QUEUE.read_text(encoding='utf-8')
        before = numbered(before_raw)
        check(len(before) == 2,
              'the fixture puts two real taskstore rows on the queue, got %r' % before_raw)
        res = sr.roll_up_triage(uncovered(11), today=TODAY)
        after_raw = ts.QUEUE.read_text(encoding='utf-8')
        after = numbered(after_raw)
        check(len(after) == len(before) + 1,
              'the roll-up adds exactly one numbered row, got %r' % [i for _r, i in after])
        check(after_raw.splitlines()[0] == before_raw.splitlines()[0],
              'the queue header line is untouched, got %r' % after_raw.splitlines()[:2])
        if not (after and after[0][1] == str(res.get('task_id'))):
            raise AssertionError('the new row must be the FIRST numbered row (the task asks for top '
                                'of QUEUE.md), got %r' % [i for _r, i in after])
        check(bool(after) and 'roll-up' in after[0][0],
              'the new row says it is the roll-up, got %r' % (after[0][0] if after else ''))
        check([i for _r, i in after[1:]] == [i for _r, i in before],
              'the existing rows keep their ids in order, got %r' % [i for _r, i in after[1:]])
        check([row_text(r) for r, _i in after[1:]] == [row_text(r) for r, _i in before],
              'existing rows keep their text; only the numbering taskstore owns may shift, got %r'
              % [r for r, _i in after[1:]])


def test_a_second_sweep_the_same_day_mints_no_second_task():
    with tempfile.TemporaryDirectory() as tmp:
        store(tmp)
        first = sr.roll_up_triage(uncovered(11), today=TODAY)
        second = sr.roll_up_triage(uncovered(11), today=TODAY)
        check(len(files_in(tmp)) == 1,
              'the second sweep writes no second task, wrote %r' % files_in(tmp))
        check(second.get('filed') is False,
              'the second sweep says it did not file again, got %r' % (second.get('filed'),))
        check(second.get('count') == 11,
              'the second sweep still counts 11, got %r' % (second.get('count'),))
        check(second.get('path') == first.get('path'),
              'the second sweep points at the roll-up that exists, got %r vs %r'
              % (second.get('path'), first.get('path')))
        check(second.get('task_id') == first.get('task_id'),
              'the second sweep reuses the existing id, got %r vs %r'
              % (second.get('task_id'), first.get('task_id')))
        check(len(numbered(ts.QUEUE.read_text(encoding='utf-8'))) == 1,
              'the queue gains one row across BOTH sweeps, got %r'
              % ([i for _r, i in numbered(ts.QUEUE.read_text(encoding='utf-8'))],))


def test_a_sweep_on_the_next_day_files_a_second_roll_up():
    with tempfile.TemporaryDirectory() as tmp:
        store(tmp)
        first = sr.roll_up_triage(uncovered(11), today=TODAY)
        second = sr.roll_up_triage(uncovered(11), today=NEXT_DAY)
        check(len(files_in(tmp)) == 2,
              'a sweep on a later day has not already run that day, so it files; wrote %r'
              % files_in(tmp))
        check(second.get('filed') is True and second.get('path') != first.get('path'),
              'the next day gets its own roll-up, got %r vs %r'
              % (second.get('path'), first.get('path')))
        p = Path(str(second.get('path') or 'missing'))
        check(p.is_file(),
              'the second sweep reports a task file that exists, got %r' % (second.get('path'),))
        if p.is_file():
            check(NEXT_DAY in p.read_text(encoding='utf-8'),
                  'the second roll-up names its own date in its body')
        check(len(numbered(ts.QUEUE.read_text(encoding='utf-8'))) == 2,
              'each day gets its own queue row, got %r'
              % ([i for _r, i in numbered(ts.QUEUE.read_text(encoding='utf-8'))],))


def test_an_rca_task_dated_today_does_not_swallow_the_roll_up():
    with tempfile.TemporaryDirectory() as tmp:
        store(tmp)
        rca_id, rca_path = rca_task(tmp)
        before = rca_path.read_text(encoding='utf-8')
        f = uncovered(11)
        res = sr.roll_up_triage(f, today=TODAY)
        if res.get('filed') is not True:
            raise AssertionError('an unrelated session-rca task dated %s must not block the '
                                'roll-up: the dedupe is a marker line in the BODY, not source + '
                                'created date (the retired oracle\'s defect); got filed=%r'
                                % (TODAY, res.get('filed')))
        check(str(res.get('task_id')) != rca_id,
              'the roll-up files its own id, not the RCA %s, got %r'
              % (rca_id, res.get('task_id')))
        check(str(res.get('path')) != str(rca_path),
              'the roll-up writes its own file, not the RCA task file')
        check(len(files_in(tmp)) == 2,
              'the roll-up is a SECOND file alongside the RCA task, got %r' % files_in(tmp))
        check(rca_path.read_text(encoding='utf-8') == before,
              'the RCA task file is not written into')
        for rec in f:
            check(rec.get('covered_by') == str(res.get('task_id')),
                  '%s is stamped with the roll-up id, got %r'
                  % (rec['key'], rec.get('covered_by')))


def _main():
    for name in sorted(globals()):
        if name.startswith('test_') and callable(globals()[name]):
            print('running %s' % name)
            try:
                globals()[name]()
            except Exception as exc:
                msg = '%s raised %s: %s' % (name, type(exc).__name__, exc)
                _failures.append(msg)
                print('FAIL: ' + msg)
    if _failures:
        print('\n%d FAILURE(S)' % len(_failures))
        return 1
    print('OK')
    return 0


if __name__ == '__main__':
    sys.exit(_main())
