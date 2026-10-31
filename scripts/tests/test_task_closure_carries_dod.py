#!/usr/bin/env python3
'''t1042 -- refuse task closure while a task's own definition-of-done has an unmet clause.

RED ON MAIN FOR EXACTLY ONE REASON: scripts/check_workspace.py defines no dod_clauses,
closure_carries_dod, or check_task_closure_dod, so a task can be closed with clauses of its
own DEFINITION OF DONE left unmet. Every test below that gate reads the REAL t244 task file
from disk, so no synthetic phrasing can be satisfied by a pattern invented to fit a fixture.
See workspace/rca/52ed69168259.md section 2: t244 is status: done (completed:
2026-10-01T184907Z), its notes carry a DEFINITION OF DONE listing clauses (a), (b) and (c),
and the 2026-10-01 close note cites a file:line for (a) alone.

Run: python3 scripts/tests/test_task_closure_carries_dod.py   (pytest is not installed here)
'''
import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))

import check_workspace as cw  # noqa: E402

REAL = REPO / 'workspace' / 'tasks' / 'done' / 't244--every-skill-description-overruns-the-index-windo.md'

DOD_NOTE = ('**2026-09-08 - manager** - DEFINITION OF DONE, so this can be picked up cold: '
            '(a) every Ollama consumer in the repo acquires the lease; '
            '(b) skills.py appears in harness_inventory; '
            '(c) a test that a second consumer is refused while the lease is held.')
CLOSE_PARTIAL = ('**2026-10-01 - claude-code** - Closed 2026-10-01 verifying the manager\'s (a): '
                 'scripts/skills.py:128 indexes the whole description; the [:400] window is gone.')
CLOSE_ALL = ('**2026-10-01 - claude-code** - Closed 2026-10-01. (a) python3 scripts/modellease.py '
             '--acquire; (b) python3 scripts/harness_inventory.py; '
             '(c) python3 scripts/tests/test_lease_refusal.py')
LETTER = re.compile(r'\(([a-z])\)')


def _task(status, completed, notes):
    head = ['---', 'id: t900', 'title: synthetic', 'status: ' + status]
    if completed:
        head.append('completed: ' + completed)
    head += ['---', '', '## Notes', '']
    return '\n'.join(head) + notes + '\n'


def test_dod_clauses_reads_the_real_t244_block():
    clauses = cw.dod_clauses(REAL.read_text(encoding='utf-8'))
    assert len(clauses) == 3, 'expected 3 clauses in the real t244 DEFINITION OF DONE note, got %d: %r' % (len(clauses), clauses)
    letters = [c[0] for c in clauses]
    assert letters == ['a', 'b', 'c'], 'clauses must come back in document order, got %r' % (letters,)
    texts = [c[1] for c in clauses]
    assert len(set(texts)) == 3, 'each clause must carry its OWN text; identical texts mean the split landed on a placeholder: %r' % (texts,)
    assert all(len(t) > 20 for t in texts), 'clause text too short to be the clause itself: %r' % (texts,)


def test_dod_clauses_accepts_a_markdown_heading_form():
    text = '## Definition of done\n(a) first requirement, long enough to be the real text\n(b) second requirement, long enough to be the real text\n'
    letters = [c[0] for c in cw.dod_clauses(text)]
    assert letters == ['a', 'b'], 'the heading form must parse too, not only the bold-prose form: %r' % (letters,)


def test_closure_reports_the_unmet_clauses_of_a_partial_close():
    # SYNTHETIC, NOT THE LIVE t244 (CLAUDE 2026-10-29, t1042): the real t244 file was later edited, so an
    # assertion on its state went red with the gate working. The property is what is pinned: a closed
    # task whose close note cites a command for (a) only is reported for (b) and (c), by its own id.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 't900--synthetic.md'
        p.write_text(_task('done', '2026-10-01T000000Z', DOD_NOTE + '\n\n' + CLOSE_PARTIAL), encoding='utf-8')
        findings = cw.closure_carries_dod(p)
    assert findings, 'a closed task with clauses (b) and (c) uncited in its close note -- a gate that returns nothing for it is not a gate'
    letters = sorted(set(LETTER.findall(' '.join(findings))))
    assert letters == ['b', 'c'], 'expected exactly clauses b and c reported, got %r in: %r' % (letters, findings)
    assert all('close note' in f for f in findings), 'each finding must say what is missing (no command cited in the close note): %r' % (findings,)
    assert all('t900' in f for f in findings), 'each finding must name the task id from front-matter: %r' % (findings,)


def test_closure_is_silent_when_every_clause_is_cited():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 't900--synthetic.md'
        p.write_text(_task('done', '2026-10-01T000000Z', DOD_NOTE + '\n\n' + CLOSE_ALL), encoding='utf-8')
        assert cw.closure_carries_dod(p) == [], 'a closed task whose close note cites a command for every clause must pass'


def test_closure_is_silent_for_an_open_task():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 't900--synthetic.md'
        p.write_text(_task('queued', '', DOD_NOTE), encoding='utf-8')
        assert cw.closure_carries_dod(p) == [], 'an OPEN task must not be reported: the rule is about closure'


def test_closure_is_silent_when_there_is_no_dod_block():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / 't900--synthetic.md'
        p.write_text(_task('done', '2026-10-01T000000Z', '**2026-10-01 - x** - Closed. scripts/x.py:1 did the thing.'), encoding='utf-8')
        assert cw.closure_carries_dod(p) == [], 'a closed task with no DEFINITION OF DONE block has no clauses to check'


def test_checker_prints_the_finding():
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / 'repo'
        tasks = repo / 'workspace' / 'tasks'
        tasks.mkdir(parents=True)
        (tasks / 't900--synthetic.md').write_text(_task('done', '2026-10-01T000000Z', DOD_NOTE + '\n\n' + CLOSE_PARTIAL), encoding='utf-8')
        chk = cw.Checker(repo)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            chk.check_task_closure_dod()
        out = buf.getvalue()
    assert 't900' in out, 'the checker must name the offending task: %r' % (out,)
    assert '[FAIL]' in out, 'an unmet closure must FAIL the workspace check, not warn: %r' % (out,)
    assert 'PASS' not in out, 'a closed task with unmet clauses must not report PASS: %r' % (out,)
    assert 'close note' in out, 'the printed detail must say what is missing: %r' % (out,)


def main():
    for name in ('dod_clauses', 'closure_carries_dod', 'check_task_closure_dod'):
        if not hasattr(cw, name):
            print('RED (expected before the fix): scripts/check_workspace.py defines no %s' % name)
            print('t1042: a task closure is not checked against the task\'s own definition of done.')
            return 1
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failed = []
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, e))
            print('%s %s: %s: %s' % ('FAIL' if isinstance(e, AssertionError) else 'ERROR', t.__name__, type(e).__name__, e))
    print('%d/%d passed' % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
