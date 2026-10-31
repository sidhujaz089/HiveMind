#!/usr/bin/env python3
'''Handover coverage: a handover write records which turns it covers, and the newest
record for a session can be read back.

WHY (t1030, from workspace/rca/2ef839117b53.md:7). The deferral pinned on this suite:

    "I'll refresh the handover at around 85% so it's current well before
     compression, rather than racing it."

The refresh happened (565b26a7, 203b9876, b58c71aa, a96fa207), and the same session's own
words say both 'Handover C is current' (18:11Z) and, four hours later, that it 'does not yet
include today's last hour' (22:25Z). Neither is checkable: nothing on disk states which turns
a given handover covers. Coverage that decays and is never recorded cannot be asserted once and
relied on at the compression boundary.

THE CONTRACT THIS SUITE PINS, in scripts/handover.py (the handover-write endpoint):

    record_coverage(root, session, file, asserted_at_turn, covered_through_turn, head_sha)
        -> the dict that was appended
    latest_coverage(root, session) -> dict | None   (the LAST row written for that session)

One JSON object per line, appended to <root>/workspace/ledgers/handover-coverage.jsonl, with
keys: session, file, asserted_at_turn, covered_through_turn, head_sha, ts.

RED ON MAIN for one reason: scripts/handover.py (153 lines: TODO, LEDGER_STATES, _ledger_body,
_ledger_rows, check_ledger, draft, check) defines neither function.

Authorship: drafted by the Manager lane for t1030. The implementation is a separate dispatch
and may not edit this file (D-35).
'''

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Import `handover` the way the frozen sibling suite does (test_handover_guard.py:29):
# parent.parent is scripts/, the directory the module really lives on.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import handover  # noqa: E402

# The verbatim quote this fixture is pinned on (workspace/rca/2ef839117b53.md:7).
QUOTE = ('I\'ll refresh the handover at around 85% so it\'s current well before '
         'compression, rather than racing it.')

# Real values from the finding, not placeholders: session aeef1f57-5a5c-47b9-9d13-6a9c6fc9b373,
# turn 534 of 602, discharged by 565b26a7, tail carried by a96fa207 into handover D.
SESSION = 'aeef1f57'
OTHER_SESSION = 'd962bb7c'          # a second real session id, named in handover.py's own comment block
HANDOVER_C = 'workspace/HANDOVER-2026-09-02-C.md'
HANDOVER_D = 'workspace/HANDOVER-2026-09-02-D.md'
TURN_ASSERTED = 534                 # 'turn 534 of 602' -- where the promise was made
TURN_LAST = 602                     # the session's last turn: the coverage the promise owed
TURN_TAIL = 688                     # later work carried into handover D (a96fa207)
HEAD_SHA = '565b26a7'
TAIL_SHA = 'a96fa207'

LEDGER_REL = Path('workspace') / 'ledgers' / 'handover-coverage.jsonl'
REQUIRED_KEYS = ('session', 'file', 'asserted_at_turn', 'covered_through_turn',
                 'head_sha', 'ts')


def _make_root(tmp: Path) -> Path:
    '''A throwaway repo root. workspace/ledgers/ is deliberately ABSENT: the writer must
    create the directory it records into, exactly as it must on a fresh clone.'''
    (tmp / 'workspace').mkdir(parents=True)
    return tmp


def _lines(root: Path) -> list:
    path = root / LEDGER_REL
    assert path.exists(), (
        f'record_coverage wrote nothing at {LEDGER_REL}; tree is '
        f'{sorted(str(p.relative_to(root)) for p in root.rglob("*"))}'
    )
    return [ln for ln in path.read_text(encoding='utf-8').splitlines() if ln.strip()]


def _record(root, session, file, asserted_at_turn, covered_through_turn, head_sha):
    '''One place the signature is spelled, so every case pins the same contract.'''
    return handover.record_coverage(root, session, file, asserted_at_turn,
                                    covered_through_turn, head_sha)


def case_api_exists(root: Path) -> None:
    '''1. The handover-write endpoint has a writer AND a reader.'''
    assert hasattr(handover, 'record_coverage'), (
        'scripts/handover.py defines no record_coverage(): the endpoint that writes a '
        f'handover records no coverage, so the claim pinned on this suite ({QUOTE!r}) can be '
        'made and then go stale with nothing on disk to check it against.'
    )
    assert hasattr(handover, 'latest_coverage'), (
        'scripts/handover.py defines no latest_coverage(): with only a writer, coverage is '
        'recorded and never read back, so no close can ask which turns the newest handover '
        'covers -- which is the question the 18:11Z/22:25Z pair in the RCA answers by memory.'
    )


def case_row_records_the_facts(root: Path) -> None:
    '''2. One write appends one JSON row carrying the real values, whole, at the stated path.'''
    row = _record(root, SESSION, HANDOVER_C, TURN_ASSERTED, TURN_LAST, HEAD_SHA)
    lines = _lines(root)
    assert len(lines) == 1, f'expected exactly 1 ledger line, got {len(lines)}: {lines!r}'
    rec = json.loads(lines[0])
    missing = [k for k in REQUIRED_KEYS if k not in rec]
    assert not missing, f'row is missing required fields {missing}; got keys {sorted(rec)}'
    assert rec['session'] == SESSION, f"session: expected {SESSION!r}, got {rec['session']!r}"
    assert rec['file'] == HANDOVER_C, f"file: expected {HANDOVER_C!r}, got {rec['file']!r}"
    assert rec['asserted_at_turn'] == TURN_ASSERTED, (
        f'asserted_at_turn: expected {TURN_ASSERTED} (the turn the promise was made), '
        f'got {rec["asserted_at_turn"]!r}'
    )
    assert rec['covered_through_turn'] == TURN_LAST, (
        f'covered_through_turn: expected {TURN_LAST}, got {rec["covered_through_turn"]!r}'
    )
    assert rec['head_sha'] == HEAD_SHA, (
        f'head_sha: expected {HEAD_SHA!r} (the commit that discharged the promise), '
        f'got {rec["head_sha"]!r}'
    )
    ts = rec['ts']
    assert isinstance(ts, str) and ts, f'ts must be a non-empty string, got {ts!r}'
    try:
        datetime.fromisoformat(ts)
    except ValueError as exc:
        raise AssertionError(f'ts {ts!r} is not an ISO-8601 timestamp: {exc}')
    assert isinstance(row, dict), (
        f'record_coverage must return the row it wrote, got {type(row).__name__}'
    )
    assert row.get('file') == HANDOVER_C and row.get('covered_through_turn') == TURN_LAST, (
        f'returned row does not carry what was recorded: {row!r}'
    )


def case_writes_append_never_replace(root: Path) -> None:
    '''3. A second handover must not erase the first one's coverage.'''
    _record(root, SESSION, HANDOVER_C, TURN_ASSERTED, TURN_LAST, HEAD_SHA)
    _record(root, SESSION, HANDOVER_D, TURN_LAST, TURN_TAIL, TAIL_SHA)
    lines = _lines(root)
    assert len(lines) == 2, (
        f'expected 2 ledger lines after two handover writes, got {len(lines)}: {lines!r} -- '
        'a ledger opened for writing (mode "w") throws away the coverage of every earlier '
        'handover, which is the decay this task exists to make visible'
    )
    recs = [json.loads(ln) for ln in lines]
    assert recs[0]['file'] == HANDOVER_C and recs[1]['file'] == HANDOVER_D, (
        f'rows must keep file order: expected {[HANDOVER_C, HANDOVER_D]}, '
        f'got {[r.get("file") for r in recs]}'
    )
    assert recs[1]['covered_through_turn'] == TURN_TAIL, (
        f'the handover D row must carry {TURN_TAIL}, got {recs[1].get("covered_through_turn")!r}'
    )


def case_latest_is_the_newest_row(root: Path) -> None:
    '''4. The reader answers "what does the NEWEST handover cover", not the first row.'''
    _record(root, SESSION, HANDOVER_C, TURN_ASSERTED, TURN_LAST, HEAD_SHA)
    _record(root, SESSION, HANDOVER_D, TURN_LAST, TURN_TAIL, TAIL_SHA)
    newest = handover.latest_coverage(root, SESSION)
    assert isinstance(newest, dict), (
        f'latest_coverage must return a dict for a session that has rows, '
        f'got {type(newest).__name__}'
    )
    assert newest.get('file') == HANDOVER_D, (
        f'latest_coverage returned the oldest row ({newest.get("file")!r}); a close gate '
        f'asked at the boundary needs the newest handover ({HANDOVER_D}), which is the one '
        'that carries the tail (a96fa207)'
    )
    assert newest.get('covered_through_turn') == TURN_TAIL, (
        f'newest coverage must be {TURN_TAIL}, got {newest.get("covered_through_turn")!r}'
    )


def case_rows_are_scoped_to_their_session(root: Path) -> None:
    '''5. Two sessions sharing one ledger must not answer for each other.'''
    _record(root, SESSION, HANDOVER_C, TURN_ASSERTED, TURN_LAST, HEAD_SHA)
    _record(root, OTHER_SESSION, HANDOVER_C, 40, 77, 'b58c71aa')
    mine = handover.latest_coverage(root, SESSION)
    theirs = handover.latest_coverage(root, OTHER_SESSION)
    assert isinstance(mine, dict) and mine.get('covered_through_turn') == TURN_LAST, (
        f'coverage leaked across sessions: {SESSION} got {mine!r}'
    )
    assert isinstance(theirs, dict) and theirs.get('covered_through_turn') == 77, (
        f'coverage leaked across sessions: {OTHER_SESSION} got {theirs!r}'
    )
    assert theirs.get('session') == OTHER_SESSION, (
        f'row for {OTHER_SESSION} reports session {theirs.get("session")!r}'
    )
    assert handover.latest_coverage(root, '00000000') is None, (
        'latest_coverage must return None for a session with no rows, not raise'
    )


def case_reading_does_not_invent_a_ledger(root: Path) -> None:
    '''6. No ledger yet: the reader says so. workspace/ledgers/ has no
    handover-coverage.jsonl today, so a reader that raises here breaks every session close,
    and one that creates the file on a read is writing a record that has nothing in it.'''
    assert handover.latest_coverage(root, SESSION) is None, (
        f'latest_coverage must return None when no ledger exists, got '
        f'{handover.latest_coverage(root, SESSION)!r}'
    )
    assert not (root / LEDGER_REL).exists(), (
        'latest_coverage created a ledger on a read -- a record must be written by the '
        'endpoint that has the facts, not by the reader'
    )


def main() -> int:
    cases = [
        ('api_exists', case_api_exists),
        ('row_records_the_facts', case_row_records_the_facts),
        ('writes_append_never_replace', case_writes_append_never_replace),
        ('latest_is_the_newest_row', case_latest_is_the_newest_row),
        ('rows_are_scoped_to_their_session', case_rows_are_scoped_to_their_session),
        ('reading_does_not_invent_a_ledger', case_reading_does_not_invent_a_ledger),
    ]
    failures = 0
    for name, fn in cases:
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(_make_root(Path(td)))
                print(f'[ok] {name}')
            except AssertionError as exc:
                print(f'[BAD] {name}: {exc}')
                failures += 1
            except Exception as exc:
                print(f'[BAD] {name}: unexpected {type(exc).__name__}: {exc}')
                failures += 1
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
