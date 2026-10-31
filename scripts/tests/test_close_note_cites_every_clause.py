#!/usr/bin/env python3
'''t1168 -- a CLOSED task's close note must answer every clause of its own DEFINITION OF DONE.

RED ON MAIN FOR EXACTLY ONE REASON, and it is not a bug in this suite. The real file
workspace/tasks/done/t244--every-skill-description-overruns-the-index-windo.md is status: done
(line 6), carries a three-clause DEFINITION OF DONE (line 27), and its close note (line 31, the
bold line stamped with the front-matter completed: date) names a command for clause (a) alone.
scripts/check_workspace.py:242 counts a clause as answered only when the literal (b) / (c) appears
in that one line AND the line carries a path token, so closure_carries_dod(REAL) returns today's
two findings, (b) and (c). Verified 2026-10-14 on HEAD 4d5fd381.

No code change is involved: the fix is evidence, not behaviour. The commands that verify (b) and
(c) were run on 2026-10-14 and pass: scripts/harness_inventory.py lists scripts/skills.py, and
scripts/tests/test_preflight_held_lease.py (4 passed, exit 0) and scripts/tests/test_modellease.py
(37 passed, exit 0) prove a second consumer is refused (modellease.Held) or waits (exit 4) while
the lease is held, which is clause (c) as the task wrote it. Amending the close note to cite them
is what turns this suite green.

It reads the REAL t244 file, never a fixture phrased to suit the checker: a synthetic phrasing is
what let a gate pass while consulting nothing (docs/skills/writing-oracles-for-delegation). The
front-matter and close-note lookups below mirror scripts/check_workspace.py:218-236 line for
line, so the suite cannot drift from the gate it tests.

Run: python3 scripts/tests/test_close_note_cites_every_clause.py   (pytest is not installed)
'''
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))

import check_workspace as cw  # noqa: E402

REAL = REPO / 'workspace' / 'tasks' / 'done' / 't244--every-skill-description-overruns-the-index-windo.md'
LETTER = re.compile(r'\(( [a-z])\)'.replace(' ', ''))


def _front_matter(lines):
    '''Mirror of scripts/check_workspace.py:218-223. Front matter is the text between the first
    pair of --- lines, read as text: taskstore writes it, and a YAML dependency would fail on
    exactly the files this gate reads (no third-party yaml in this venv).'''
    fm = {}
    bounds = [n for n, line in enumerate(lines) if line.strip() == '---']
    if len(bounds) >= 2:
        for line in lines[bounds[0] + 1:bounds[1]]:
            key, sep, val = line.partition(':')
            if sep and key.strip() and not line[:1].isspace():
                fm[key.strip()] = val.strip()
    return fm


def _close_note(lines, stamp):
    '''Mirror of scripts/check_workspace.py:229-236: the LAST bold line whose characters 2:12
    equal the front-matter completed date; with no stamp, the last bold line in the file.'''
    close = ''
    for line in lines:
        if not line.startswith('**'):
            continue
        if stamp and line[2:12] != stamp:
            continue
        close = line
    return close


def _text():
    return REAL.read_text(encoding='utf-8')


def test_the_real_t244_is_closed_and_carries_three_distinct_clauses():
    text = _text()
    assert text.strip(), 'the file the hourly check named is empty: %s' % REAL
    fm = _front_matter(text.splitlines())
    assert fm.get('status', '').lower() in cw.CLOSED_STATUSES, (
        'this suite only means something for a CLOSED task; t244 status is %r' % fm.get('status'))
    clauses = cw.dod_clauses(text)
    assert [c[0] for c in clauses] == ['a', 'b', 'c'], (
        't244 must carry clauses (a) (b) (c) for this suite to test anything: %r'
        % ([c[0] for c in clauses],))
    texts = [c[1] for c in clauses]
    assert len(set(texts)) == 3, (
        'the clauses must be three distinct requirements, not one placeholder: %r' % (texts,))
    assert all(len(t) > 20 for t in texts), (
        'clause text too short to be the clause itself: %r' % (texts,))


def test_the_close_note_names_a_command_inside_every_clause():
    '''The three checks a person would make by hand: the letter is on the ONE line the gate
    reads; its OWN span cites a command, because one citation cannot answer three clauses (the
    t297 / t370 shape); and every cited path exists on disk, so evidence that names a file which
    does not exist -- scripts/tests/test_lease_refusal.py, a name invented by an earlier fixture
    -- cannot be merged.'''
    lines = _text().splitlines()
    fm = _front_matter(lines)
    stamp = fm.get('completed', '')[:10]
    note = _close_note(lines, stamp)
    assert note, (
        'no bold line is readable as the close note of t244 (completed: %r) -- the gate reads the '
        'last bold line whose characters 2:12 equal the completed date' % fm.get('completed'))
    for letter, clause in cw.dod_clauses(_text()):
        assert ('(%s)' % letter) in note, (
            'the close note does not answer clause (%s): %s' % (letter, ' '.join(clause.split())))
    marks = list(LETTER.finditer(note))
    for n, m in enumerate(marks):
        letter = m.group(1)
        if letter not in ('a', 'b', 'c'):
            continue
        end = marks[n + 1].start() if n + 1 < len(marks) else len(note)
        span = note[m.end():end]
        assert cw.PATH_TOKEN.search(span), (
            'clause (%s) is named with no command in its own span, so the letter answers '
            'nothing: %r' % (letter, span.strip()))
    for token in cw.PATH_TOKEN.findall(note):
        assert (REPO / token).is_file(), (
            'the close note cites %s, which is not a file in this repository' % token)


def test_the_gate_is_silent_on_the_closed_task():
    findings = cw.closure_carries_dod(REAL)
    assert findings == [], (
        'the gate still reports %d finding(s) for t244: %s' % (len(findings), ' | '.join(findings)))


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    failed = []
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, e))
            print('%s %s: %s: %s' % ('FAIL' if isinstance(e, AssertionError) else 'ERROR',
                                     t.__name__, type(e).__name__, e))
    print('%d/%d passed' % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
