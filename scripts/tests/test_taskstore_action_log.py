#!/usr/bin/env python3
'''t106 -- the task store's action log: one row per state change, none per read.

Run: python3 scripts/tests/test_taskstore_action_log.py   (pytest is not installed here)

Red on main for ONE reason: projects/app-repo/taskstore.py has no action log at all -- no
log_action, no ACTION_LOG, and no state-changing function appends a row. Verified 2026-10-19:
grep -rn 'action_log' over projects/app-repo/ --include=*.py returns no hits, and grep -n 'log'
over projects/app-repo/taskstore.py returns no matches at all.

Every test points the store's module-level paths (TASKS, DONE, QUEUE, LOCK, ATTACH, ACTION_LOG)
at its own temp dir, so nothing here touches the live repository: no real task file, no real
QUEUE.md, no real .lock, no RESOLVED.md.
'''
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'projects' / 'app-repo'))
import taskstore as ts   # noqa: E402

FAILURES = []


def check(cond, msg):
    if not cond:
        FAILURES.append(msg)
        print('FAIL: ' + msg)
    return bool(cond)


def fresh_store():
    '''Point every path the store writes at a fresh temp dir.

    These are module-level constants read at call time inside the store, so reassigning them
    redirects every write the tests make. write_queue([]) is the store's OWN writer, so the
    queue file is written in the store's format rather than a format this test invents.
    '''
    tasks = Path(tempfile.mkdtemp(prefix='t106-')) / 'tasks'
    tasks.mkdir(parents=True, exist_ok=True)
    ts.TASKS = tasks
    ts.DONE = tasks / 'done'
    ts.QUEUE = tasks / 'QUEUE.md'
    ts.LOCK = tasks / '.lock'
    ts.ATTACH = tasks / 'attachments'
    ts.ACTION_LOG = tasks / 'action-log.jsonl'
    ts.write_queue([])


def rows_of():
    '''The rows in the log, as parsed dicts. An absent file is zero rows; a line that is not
    JSON comes back as an {'unparseable': ...} marker, so a wrong implementation FAILS an
    assertion here instead of crashing the run.'''
    log = Path(ts.ACTION_LOG)
    if not log.is_file():
        return []
    out = []
    for line in log.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError as exc:
            out.append({'unparseable': line, 'why': str(exc)})
    return out


def test_log_action_exists():
    check(callable(getattr(ts, 'log_action', None)),
          'taskstore.log_action must exist and be callable -- the store has no action log today')


def test_add_writes_one_row():
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix='t106-add-')
        fresh_store()
        tid = ts.add('Log every action', area='design', source='t106 oracle')
        rows = rows_of()
        if not check(len(rows) == 1, 'add() must append exactly one row; found ' + str(len(rows))):
            return
        r = rows[0]
        check(set(r) == {'ts', 'action', 'task', 'who', 'detail'},
              'a row must carry exactly ts/action/task/who/detail; found ' + str(sorted(r)))
        check(r.get('action') == 'add', "add() must log action='add'; found " + repr(r.get('action')))
        check(r.get('task') == tid, 'the row must name the task created (' + repr(tid) + '); found ' + repr(r.get('task')))
        check(str(r.get('detail')) == 'Log every action',
              "add() must log the title as detail; found " + repr(r.get('detail')))
        check(bool(str(r.get('ts', '')).strip()), 'row ts must be a non-empty stamp')
        check(bool(str(r.get('who', '')).strip()), 'row who must be a non-empty identity')
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def test_each_action_names_what_it_was():
    '''Four actions, four distinct details. One repeated placeholder string would satisfy a
    test that only checks a row exists; this checks each row carries ITS OWN values.'''
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix='t106-names-')
        fresh_store()
        tid = ts.add('Naming', source='t106 oracle')
        check(ts.add_note(tid, 'a note', who='claude-code') is True, 'add_note on a real task must return True')
        check(ts.add_attachment(tid, 'photo.png') is True, 'add_attachment on a real task must return True')
        check(ts.set_meta(tid, area='build', who='claude-code') is True, 'set_meta on a real task must return True')
        rows = rows_of()
        if not check(len(rows) == 4, 'four state changes must write four rows; found ' + str(len(rows))):
            return
        actions = [r.get('action') for r in rows]
        check(actions == ['add', 'note', 'attachment', 'meta'],
              'actions in order must be add/note/attachment/meta; found ' + repr(actions))
        details = [str(r.get('detail')) for r in rows]
        check(len(set(details)) == len(details),
              'each row must carry its own action\'s value, not a repeated placeholder: ' + repr(details))
        check(details[1] == 'a note', 'the note row must carry the note text; found ' + repr(details[1]))
        check(details[2] == 'photo.png', 'the attachment row must carry the filename; found ' + repr(details[2]))
        check('area=build' in details[3], "the meta row must say what was set (area=build); found " + repr(details[3]))
        check(rows[1].get('who') == 'claude-code',
              'the note row must inherit the note\'s own who; found ' + repr(rows[1].get('who')))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def test_reads_write_nothing():
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix='t106-read-')
        fresh_store()
        tid = ts.add('Reads', source='t106 oracle')
        before = len(rows_of())
        check(before == 1, 'setup: one add must have written one row; found ' + str(before))
        ts.find(tid)
        ts.find('t999')
        ts.read_queue()
        ts.load_all()
        ts.ordered()
        ts.parse(Path(str(ts.ACTION_LOG)))
        after = len(rows_of())
        check(after == before,
              'a read must append nothing: ' + str(before) + ' rows before the reads, ' + str(after) + ' after')
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def test_failed_action_writes_no_row():
    '''An action that changed nothing must not be recorded as if it happened.'''
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix='t106-fail-')
        fresh_store()
        check(ts.add_note('t999', 'no such task', who='claude-code') is False, 'add_note on a missing task must return False')
        check(ts.set_meta('t999', area='build', who='claude-code') is False, 'set_meta on a missing task must return False')
        check(ts.add_attachment('t999', 'ghost.png') is False, 'add_attachment on a missing task must return False')
        rows = rows_of()
        check(rows == [], 'an action that changed nothing must write no row; found ' + str(len(rows)) + ' rows: ' + str(rows))
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def test_long_note_is_logged_whole():
    '''detail is the WHOLE text. A slice is a truncated record: docs/skills/never-truncate-a-record.'''
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix='t106-long-')
        fresh_store()
        tid = ts.add('Long', source='t106 oracle')
        text = chr(10).join('line %d of a long note that must arrive whole' % i for i in range(120))
        check(ts.add_note(tid, text, who='claude-code') is True, 'add_note with a long note must return True')
        note_rows = [r for r in rows_of() if r.get('action') == 'note']
        if not check(len(note_rows) == 1, 'one note must write exactly one row; found ' + str(len(note_rows))):
            return
        check(note_rows[0].get('detail') == text,
              'the note row\'s detail must be the whole note (' + str(len(text))
              + ' chars); a sliced detail is a truncated record')
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def test_log_lines_are_valid_json():
    tmp = None
    try:
        tmp = tempfile.mkdtemp(prefix='t106-json-')
        fresh_store()
        ts.add('JSON', source='t106 oracle')
        raw = Path(ts.ACTION_LOG).read_text(encoding='utf-8').splitlines()
        if not check(len(raw) >= 1, 'the log file must exist with at least one line'):
            return
        bad = [i for i, line in enumerate(raw, 1) if line.strip() and 'unparseable' in json.dumps(_safe(line))]
        for i, line in enumerate(raw, 1):
            try:
                json.loads(line)
            except ValueError as exc:
                check(False, 'log line ' + str(i) + ' is not valid JSON (' + str(exc) + '): ' + line[:80])
        check(not bad, 'every log line must parse as JSON; found ' + str(len(bad)) + ' bad line(s)')
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def _safe(line):
    try:
        return json.loads(line)
    except ValueError:
        return {'unparseable': True}


TESTS = [(name, fn) for name, fn in sorted(globals().items())
         if name.startswith('test_') and callable(fn)]


def main():
    for name, fn in TESTS:
        print('--- ' + name)
        fn()
    if FAILURES:
        print('FAILED ' + str(len(FAILURES)) + ' assertion(s):')
        for f in FAILURES:
            print('  - ' + f)
        return 1
    print('OK: ' + str(len(TESTS)) + ' test(s), 0 failed assertions')
    return 0


if __name__ == '__main__':
    sys.exit(main())
