#!/usr/bin/env python3
"""t1466: the handover ledger's vocabulary gains BUILT-NOT-ENFORCED and OPEN-DEFERRED, and a
CLOSED row that carries no enforcement artefact is refused at close-out.

WHY (RCA fed50d539a8b): gates 3 and 6 were built and never wired into a live caller
(gate_callers.py shows no live caller). The ledger vocabulary had no honest label for that
state, so they were written CLOSED and dropped from the carry. The binding rule: a close-out
may not assert CLOSED for a work item that lacks an enforcement artefact.

RED ON MAIN because scripts/handover.py line 28 defines LEDGER_STATES as
('CLOSED', 'FILED', 'DECLARED-OPEN'), so a row written in either new state is refused as
'no stated state', and check_ledger checks only that a row carries a state string -- it never
looks at the Artefact cell of a CLOSED row.

The three accepted-shape cases (CLOSED with a receipt, FILED, DECLARED-OPEN) are preservation:
they are green today and must stay green -- the artefact rule binds CLOSED and nothing else.

No live state: every string goes straight to check_ledger; nothing is read from disk and
nothing is written.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import handover  # noqa: E402

LEDGER_HEAD = '## Thread ledger\n'
COLUMNS = '| Thread | State | Artefact |\n|---|---|---|\n'


def _ledger(row):
    """A complete Thread ledger section carrying exactly the one data row given."""
    return LEDGER_HEAD + COLUMNS + row


def test_new_states_are_valid_ledger_states():
    for state in ('BUILT-NOT-ENFORCED', 'OPEN-DEFERRED'):
        assert state in handover.LEDGER_STATES, (
            f'{state} is not in LEDGER_STATES {handover.LEDGER_STATES}; a row written in '
            f'this state is refused today as "no stated state", so the honest label does '
            f'not exist and a built-but-never-called gate is pushed into CLOSED'
        )


def test_closed_row_with_empty_artefact_is_rejected():
    row = '| gate 6 (golden_score.py) | CLOSED | |\n'
    problems = handover.check_ledger(_ledger(row), 'test/HANDOVER-x.md')
    assert isinstance(problems, list), f'expected a list, got {type(problems).__name__}'
    assert problems, (
        'a CLOSED row with an empty Artefact cell was accepted at close-out; the tri-state '
        'rule requires an enforcement artefact for CLOSED, and BUILT-NOT-ENFORCED is the '
        'honest label for a gate that was built and never wired in'
    )
    assert any('artefact' in p.lower() for p in problems), (
        f'the refusal does not name the missing enforcement artefact, so the author cannot '
        f'act on it: {problems}'
    )


def test_closed_row_with_whitespace_artefact_is_rejected():
    row = '| gate 3 (evidence.py) | CLOSED |    |\n'
    problems = handover.check_ledger(_ledger(row), 'test/HANDOVER-x.md')
    assert problems, (
        'a whitespace-only Artefact cell reads as a receipt to a row whose state is CLOSED'
    )
    assert any('artefact' in p.lower() for p in problems), (
        f'the refusal does not say which requirement failed: {problems}'
    )


def test_closed_row_with_artefact_is_accepted():
    row = '| gate 3 (evidence.py) | CLOSED | scripts/tests/test_evidence.py passes on pre-push |\n'
    assert handover.check_ledger(_ledger(row), 'test/HANDOVER-x.md') == []


def test_built_not_enforced_row_is_accepted():
    row = '| gate 6 (golden_score.py) | BUILT-NOT-ENFORCED | gate_callers.py shows no live caller |\n'
    assert handover.check_ledger(_ledger(row), 'test/HANDOVER-x.md') == []


def test_open_deferred_row_is_accepted():
    row = '| t1466 (tri-state) | OPEN-DEFERRED | task t1466, named cover in the carry |\n'
    assert handover.check_ledger(_ledger(row), 'test/HANDOVER-x.md') == []


def test_filed_row_is_accepted():
    row = '| gate 1 (runner.py) | FILED | task t1234 |\n'
    assert handover.check_ledger(_ledger(row), 'test/HANDOVER-x.md') == []


def test_declared_open_row_is_accepted():
    row = '| gate 5 (evidence.py) | DECLARED-OPEN | carried to the next session |\n'
    assert handover.check_ledger(_ledger(row), 'test/HANDOVER-x.md') == []


if __name__ == '__main__':
    import traceback

    tests = [
        test_new_states_are_valid_ledger_states,
        test_closed_row_with_empty_artefact_is_rejected,
        test_closed_row_with_whitespace_artefact_is_rejected,
        test_closed_row_with_artefact_is_accepted,
        test_built_not_enforced_row_is_accepted,
        test_open_deferred_row_is_accepted,
        test_filed_row_is_accepted,
        test_declared_open_row_is_accepted,
    ]
    failed = []
    for t in tests:
        try:
            t()
            print(f'  PASS  {t.__name__}')
        except AssertionError as e:
            print(f'  FAIL  {t.__name__}: {e}')
            failed.append(t.__name__)
        except Exception:
            print(f'  ERROR {t.__name__}:')
            traceback.print_exc()
            failed.append(t.__name__)
    print(f'\n{len(tests) - len(failed)}/{len(tests)} passed')
    sys.exit(1 if failed else 0)
