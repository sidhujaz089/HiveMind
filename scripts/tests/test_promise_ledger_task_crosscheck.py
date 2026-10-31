#!/usr/bin/env python3
"""A deferring 'I'll' must resolve to a task that EXISTS in the task store (t1316).

WHY. Session dbd3c140-6426-4daa-85f8-e104a14c7e3c, turn 108: "I'll make the 30-second wait
configurable so a test can run it quickly, then add a 'server down' case to the existing
endpoint-drop test, reusing its clone." No task id was attached, so the commitment became
untracked the moment the turn ended. The recorder at workspace/session-reviews/promise-ledger.jsonl
already has it, verdict 'undischarged' (row 406; RCA workspace/rca/1821ef705004.md, and the
sentence is quoted verbatim in t1316's body). The recorder is the EVIDENCE; what is missing is
the join to the task store -- the CHANGE line in the task's first checklist item.

WHAT THIS PINS, and what today's gate cannot. check_declared_intent.ADDRESSED is a phrasing
gate: it accepts any tNNN token sitting near the sentence, so "t9999" satisfies it while naming
no task, and the module never opens the task store. The missing rung is the join: an undischarged
promise whose quote cites no id naming a file in workspace/tasks/ is a finding; an id naming a
real task file is silent; a promise executed in-text is silent.

WARN posture, pinned by the CLI test: this module's own docstring gives the reason -- a check
that blocks on ordinary prose gets switched off within a week, and a gate everyone routes around
is worth less than none.

Fixtures only: every file read is built with tempfile; the live ledger is never opened, and the
live task store is never listed.
Run: python3 scripts/tests/test_promise_ledger_task_crosscheck.py  (pytest is not installed)
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import check_declared_intent as cdi  # noqa: E402

SESSION = 'dbd3c140-6426-4daa-85f8-e104a14c7e3c'
# The real sentence, verbatim from row 406 of the recorder -- not a paraphrase.
QUOTE = ('I\'ll make the 30-second wait configurable so a test can run it quickly, '
         'then add a "server down" case to the existing endpoint-drop test, reusing its clone.')
ROW = {'session': SESSION, 'turn': 108, 'quote': QUOTE, 'promise_phrase': "I'll",
       'matching_actions_within_n_turns': 0, 'artefact': [],
       'verdict': 'undischarged', 'evidence_channel': 'assistant-text',
       'at': '2026-10-16T14:52:41Z'}


def test_bare_promise_is_a_finding():
    """The t1316 shape: a deferral that carried no number at all."""
    f = cdi.promise_findings([dict(ROW)], {'t1316'})
    assert len(f) == 1, 'one undischarged row with no id must give one finding, got %r' % (f,)
    one = f[0]
    assert SESSION in one, 'a finding must name the session a reader can open: %r' % (one,)
    assert 'turn 108' in one, 'a finding must name the turn so the row is findable: %r' % (one,)
    assert 'no task id' in one, 'a finding must say the promise carried NO id: %r' % (one,)


def test_an_id_that_resolves_to_a_real_task_is_silent():
    rows = [dict(ROW, quote=QUOTE + ' Filed as t1316 so it cannot be lost.')]
    f = cdi.promise_findings(rows, {'t1316'})
    assert f == [], 'a promise naming a task file that exists is not a debt, got %r' % (f,)


def test_an_id_that_resolves_to_nothing_is_a_finding():
    """The force the phrasing gate has today, and lacks: the id must name a FILE."""
    rows = [dict(ROW, quote=QUOTE + ' Filed as t9999.')]
    f = cdi.promise_findings(rows, {'t1316'})
    assert len(f) == 1, 'an id that names no task file is still a debt, got %r' % (f,)
    one = f[0]
    assert 't9999' in one, 'the finding must name the unresolved id: %r' % (one,)
    assert 'no task id' not in one, 'a cited id is a distinct case and takes a distinct message: %r' % (one,)


def test_two_ids_name_only_the_unresolved_one():
    rows = [dict(ROW, quote=QUOTE + ' Tracked on t1316 and t9999.')]
    f = cdi.promise_findings(rows, {'t1316'})
    assert len(f) == 1, 'two ids on one row are one debt, got %r' % (f,)
    one = f[0]
    assert 't9999' in one, 'the unresolved id must be named: %r' % (one,)
    assert 't1316' not in one, 'a resolved id must not be echoed; a dump-all-ids build fails here: %r' % (one,)


def test_a_promise_executed_in_text_is_not_a_debt():
    f = cdi.promise_findings([dict(ROW, verdict='executed-in-text')], {'t1316'})
    assert f == [], 'only verdict undischarged is a debt, got %r' % (f,)


def test_known_task_ids_reads_the_stores_real_filename_shape():
    """The store names tasks tNNN--slug.md. A glob of t*.md is not that shape."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / 't1316--require-a-deferring-i-ll.md').write_text('x\n', encoding='utf-8')
        (d / 't999--other-thing.md').write_text('x\n', encoding='utf-8')
        (d / 'notes.md').write_text('x\n', encoding='utf-8')
        (d / 't9999.md').write_text('x\n', encoding='utf-8')
        (d / 'sub').mkdir()
        (d / 'sub' / 't888--nested.md').write_text('x\n', encoding='utf-8')
        got = cdi.known_task_ids(d)
    assert sorted(got) == ['t1316', 't999'], 'the store shape is tNNN--slug.md: got %r' % (sorted(got),)
    assert 't9999' not in set(got), 'a file with no --slug is not a task: got %r' % (sorted(got),)
    assert 't888' not in set(got), 'the store is a flat directory, not a walk: got %r' % (sorted(got),)


def test_load_promise_rows_records_a_torn_line():
    """The recorder appends as it goes, so a session killed mid-write leaves a half line.
    A reader that swallows it reports 'no promises outstanding' -- the ledger's whole purpose
    is surviving exactly that (writing-oracles-for-delegation, failure mode 1)."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 'promise-ledger.jsonl'
        with p.open('w', encoding='utf-8') as fh:
            fh.write(json.dumps(ROW) + '\n')
            fh.write('\n')
            fh.write('{"session": "x", "turn": 2, "quote": "cut off mid\n')
        rows = cdi.load_promise_rows(p)
    assert len(rows) == 2, 'blank lines are skipped, a torn line is kept: %r' % (rows,)
    assert rows[0]['turn'] == 108, 'a whole row is returned whole: %r' % (rows[0],)
    assert rows[0]['verdict'] == 'undischarged', 'the recorder verdict survives the read: %r' % (rows[0],)
    assert 'error' in rows[1], 'a line that is not valid JSON must be RECORDED, never swallowed: %r' % (rows[1],)
    assert rows[1]['raw'].startswith('{"session": "x"'), 'the torn line keeps its own text: %r' % (rows[1],)


def test_the_paths_named_are_the_real_surfaces():
    """The RCA names this recorder as the proof the rule is in force, so the wiring target is
    part of the contract: a check pointed at a fixture measures the fixture. No disk is read."""
    assert str(cdi.PROMISE_LEDGER) == str(cdi.REPO / 'workspace' / 'session-reviews' / 'promise-ledger.jsonl'), \
        'the recorder path must be the one the RCA names: got %r' % (str(cdi.PROMISE_LEDGER),)
    assert str(cdi.TASKS_DIR) == str(cdi.REPO / 'workspace' / 'tasks'), \
        'the task store is that directory: got %r' % (str(cdi.TASKS_DIR),)


def test_the_cli_reports_the_crosscheck_and_stays_a_warn():
    r = subprocess.run([sys.executable, str(REPO / 'scripts' / 'check_declared_intent.py')],
                       capture_output=True, text=True, timeout=180, cwd=str(REPO))
    assert r.returncode == 0, 'the cross-check is a WARN, never a blocker; rc=%d stderr=%r' % (
        r.returncode, r.stderr)
    assert 'undischarged promise(s)' in r.stdout, 'the aggregate line is missing: %r' % (r.stdout,)


def test_the_existing_decay_suite_still_passes():
    """The neighbour this module's docstring names as its decay test."""
    r = subprocess.run([sys.executable, str(REPO / 'scripts' / 'tests' / 'test_declared_intent.py')],
                       capture_output=True, text=True, timeout=180, cwd=str(REPO))
    assert r.returncode == 0, "the module's own decay suite went red: rc=%d out=%r" % (
        r.returncode, r.stdout)


TESTS = [test_bare_promise_is_a_finding,
         test_an_id_that_resolves_to_a_real_task_is_silent,
         test_an_id_that_resolves_to_nothing_is_a_finding,
         test_two_ids_name_only_the_unresolved_one,
         test_a_promise_executed_in_text_is_not_a_debt,
         test_known_task_ids_reads_the_stores_real_filename_shape,
         test_load_promise_rows_records_a_torn_line,
         test_the_paths_named_are_the_real_surfaces,
         test_the_cli_reports_the_crosscheck_and_stays_a_warn,
         test_the_existing_decay_suite_still_passes]


def main():
    failed = []
    for fn in TESTS:
        try:
            fn()
            print('  [ok] %s' % fn.__name__)
        except AssertionError as exc:
            print('  [BAD] %s: %s' % (fn.__name__, exc))
            failed.append(fn.__name__)
        except Exception as exc:
            # a missing function is a finding about THIS task, and is named as one: never-truncate-a-record
            print('  [BAD] %s raised %s: %s' % (fn.__name__, type(exc).__name__, exc))
            failed.append(fn.__name__)
    print()
    if failed:
        print('FAIL: %d of %d test(s) failed: %s' % (len(failed), len(TESTS), ', '.join(failed)))
        return 1
    print('PASS: %d test(s)' % len(TESTS))
    return 0


if __name__ == '__main__':
    sys.exit(main())
