#!/usr/bin/env python3
'''t762 -- session_end.py must refuse to close a session while a population it was
handed is unaccounted for.

RCA 31c25f11372a, section 5: "A named population may not end a session unaccounted
for ... session_end.py refuses to close while any member of a row written by this
session carries no disposition (triaged, deferred -> tNNN, for-jas -> tNNN, refuted)."
"seen, not examined" has to become a state that a close-out cannot pass in, and the
disposition vocabulary has to be closed: a disposition that says 'seen' is the defect,
not the cure.

Contract it pins (to be added to scripts/session_end.py):

    ALLOWED_DISPOSITIONS = ("triaged", "deferred", "for-jas", "refuted")

    population_blockers(population_rows, disposition_rows) -> list of
        {"population": str, "item": str, "why": str}
    sorted by (population, item). Empty list means the close-out may proceed.

Run: python3 scripts/tests/test_end_population_gate.py   (no pytest; exits 1 on a failure)
'''

import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPTS_DIR = HERE.parents[1]          # scripts/  -- where session_end.py lives
REPO_ROOT = HERE.parents[2]            # repo root -- parents[2] from scripts/tests/
for _p in (str(SCRIPTS_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import session_end                      # noqa: E402

TWO_POPULATIONS = [
    {'population': 'feedback-notes', 'count': 2,
     'items': ['workspace/feedback/t700-gap.md', 'workspace/feedback/t701-gap.md']},
    {'population': 'inbox', 'count': 1,
     'items': ['workspace/inbox/2026-10-07-chat.md']},
]


def test_the_vocabulary_is_closed():
    # 'seen' must not be a legal disposition: a gate that accepts it is the same gate that
    # let 3 notes and 11 briefs end a session unexamined.
    assert tuple(session_end.ALLOWED_DISPOSITIONS) == ('triaged', 'deferred', 'for-jas', 'refuted'), \
        tuple(getattr(session_end, 'ALLOWED_DISPOSITIONS', ()))


def test_a_population_with_no_dispositions_blocks_every_member():
    got = session_end.population_blockers(TWO_POPULATIONS, [])
    assert got == [
        {'population': 'feedback-notes', 'item': 'workspace/feedback/t700-gap.md',
         'why': 'no disposition recorded'},
        {'population': 'feedback-notes', 'item': 'workspace/feedback/t701-gap.md',
         'why': 'no disposition recorded'},
        {'population': 'inbox', 'item': 'workspace/inbox/2026-10-07-chat.md',
         'why': 'no disposition recorded'},
    ], got


def test_every_allowed_disposition_clears_its_member():
    rows = [{'population': 'feedback-notes', 'count': 4,
             'items': ['a.md', 'b.md', 'c.md', 'd.md']}]
    disp = [
        {'item': 'a.md', 'disposition': 'triaged'},
        {'item': 'b.md', 'disposition': 'deferred', 'task': 't762'},
        {'item': 'c.md', 'disposition': 'for-jas', 'task': 't763'},
        {'item': 'd.md', 'disposition': 'refuted'},
    ]
    assert session_end.population_blockers(rows, disp) == [], session_end.population_blockers(rows, disp)


def test_an_unrecognised_disposition_leaves_the_member_blocked():
    rows = [{'population': 'inbox', 'count': 1, 'items': ['workspace/inbox/x.md']}]
    assert session_end.population_blockers(rows, [{'item': 'workspace/inbox/x.md',
                                                  'disposition': 'seen'}]) == [
        {'population': 'inbox', 'item': 'workspace/inbox/x.md',
         'why': "unrecognised disposition 'seen'"},
    ], session_end.population_blockers(rows, [{'item': 'workspace/inbox/x.md', 'disposition': 'seen'}])


def test_a_deferral_without_a_task_id_is_not_a_disposition():
    rows = [{'population': 'inbox', 'count': 2, 'items': ['x.md', 'y.md']}]
    got = session_end.population_blockers(rows, [
        {'item': 'x.md', 'disposition': 'deferred'},
        {'item': 'y.md', 'disposition': 'deferred', 'task': ''},
    ])
    assert [g['why'] for g in got] == ['deferred without a task id',
                                      'deferred without a task id'], got
    assert [g['item'] for g in got] == ['x.md', 'y.md'], got


def test_a_task_id_clears_a_deferral_and_a_for_jas():
    rows = [{'population': 'inbox', 'count': 2, 'items': ['x.md', 'y.md']}]
    assert session_end.population_blockers(rows, [
        {'item': 'x.md', 'disposition': 'deferred', 'task': 't762'},
        {'item': 'y.md', 'disposition': 'for-jas', 'task': 't900'},
    ]) == []


def test_a_disposition_for_a_member_that_was_never_announced_clears_nothing():
    rows = [{'population': 'inbox', 'count': 1, 'items': ['x.md']}]
    got = session_end.population_blockers(rows, [{'item': 'z.md', 'disposition': 'triaged'}])
    assert [g['item'] for g in got] == ['x.md'], got


def test_nothing_announced_and_nothing_recorded_is_not_a_blocker():
    assert session_end.population_blockers([], []) == []
    assert session_end.population_blockers([], [{'item': 'x.md', 'disposition': 'triaged'}]) == []


def test_main_uses_the_gate():
    src = (SCRIPTS_DIR / 'session_end.py').read_text(encoding='utf-8')
    parts = src.split('def main(', 1)
    assert len(parts) == 2, 'no def main( in scripts/session_end.py'
    assert 'population_blockers(' in parts[1], \
        'main() must call population_blockers() -- a gate nothing calls blocks nothing'


if __name__ == '__main__':
    _tests = [(n, f) for n, f in sorted(globals().items())
              if n.startswith('test_') and callable(f)]
    _failed = 0
    for _name, _fn in _tests:
        try:
            _fn()
            print(f'PASS {_name}')
        except Exception:
            _failed += 1
            print(f'FAIL {_name}')
            traceback.print_exc()
    print(f'{len(_tests) - _failed}/{len(_tests)} passed')
    sys.exit(1 if _failed else 0)
