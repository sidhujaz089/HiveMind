#!/usr/bin/env python3
'''t1342: scripts/error_scan.py records one ledger row per transient safety-classifier
denial, at workspace/ledgers/safety-classifier-denials.jsonl.

WHY: workspace/rca/404fb2c9f414.md -- three Bash denials in 28 seconds whose verbatim
tool_result text is 'The server-side auto mode classifier gave no verdict (error), so auto
mode cannot determine the safety of Bash. This is a transient failure of the check, not a
judgment about the action.' That outage is recorded only as prose in a transcript, so its
frequency is uncountable and no upstream gate can be justified from it.

RED ON MAIN FOR ONE REASON: error_scan has no record_denials (first test). Nothing here is
red for its own bug: fixtures are built in tempfile.TemporaryDirectory() only, there is no
subprocess, no model call, no fixed temp path, and no repository data file is read.
'''
import json
import sys
import tempfile
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import error_scan

SID = 'be2121ac-5236-4268-a5fa-56bda26324e3'
SID2 = '404fb2c9f414-0000-0000-000000000000'
DENIAL = ('The server-side auto mode classifier gave no verdict (error), so auto mode '
          'cannot determine the safety of Bash. This is a transient failure of the check, '
          'not a judgment about the action.')
UNRELATED = 'bash: line 1: python3: command not found'
# Long, punctuated mid-way: a sliced value is detectable by equality, not by length.
CMD = ('git add scripts/tests/test_coder_branch_delivery.py scripts/tests/test_coder_green_count.py'
       " && git commit -m 'fix(scripts): a test's fake model server dies with the test'"
       ' && git log --oneline -3 -- scripts/tests/'
       ' && git status --porcelain scripts/tests/ --untracked-files=no --ignored=no'
       ' && git diff --stat HEAD~1 HEAD -- scripts/')
TS = '2026-09-29T15:00:38.177Z'
KEYS = {'session', 'ts', 'tool', 'intended_command', 'reason', 'consecutive_denials'}


def use(n, sid=SID, cmd=CMD, ts='2026-09-29T15:00:36.362Z', name='Bash'):
    return {'type': 'assistant', 'sessionId': sid, 'timestamp': ts, 'uuid': 'a%d' % n,
            'message': {'content': [{'type': 'tool_use', 'id': 'toolu-%d' % n, 'name': name,
                                     'input': {'command': cmd, 'description': 'run it'}}]}}


def result(n, text=DENIAL, is_error=True, ts=TS, sid=SID):
    return {'type': 'user', 'sessionId': sid, 'timestamp': ts, 'uuid': 'r%d' % n,
            'sourceToolAssistantUUID': 'a%d' % n,
            'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'toolu-%d' % n,
                                     'is_error': is_error, 'content': text}]}}


def ledger_rows(path):
    out = []
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def transcript(td, name='transcript.jsonl'):
    f = Path(td) / name
    with f.open('w', encoding='utf-8') as fh:
        fh.write(json.dumps(use(1)) + '\n')
        fh.write(json.dumps(result(1)) + '\n')
    return str(f)


def test_record_denials_exists():
    assert hasattr(error_scan, 'record_denials'), \
        'error_scan has no record_denials -- t1342 asks the session scan to gain one'


def test_default_ledger_is_the_ledger_the_task_names():
    got = str(getattr(error_scan, 'DENIAL_LEDGER', 'MISSING')).replace(chr(92), '/')
    assert got.endswith('workspace/ledgers/safety-classifier-denials.jsonl'), \
        'DENIAL_LEDGER must be the ledger the task names, got %r' % (got,)


def test_one_denial_writes_one_row_with_the_exact_six_keys():
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        n = error_scan.record_denials([use(1), result(1)], ledger=led)
        assert n == 1, 'expected 1 row appended, got %r' % (n,)
        rows = ledger_rows(led)
        assert len(rows) == 1, 'expected exactly 1 ledger row, got %d' % len(rows)
        r = rows[0]
        assert set(r) == KEYS, 'row keys must be exactly %s, got %s' % (sorted(KEYS), sorted(r))
        assert r['session'] == SID, 'session: expected %r, got %r' % (SID, r['session'])
        assert r['ts'] == TS, 'ts must be the tool_result row\'s own timestamp %r, got %r' % (TS, r['ts'])
        assert r['tool'] == 'Bash', 'tool: expected Bash from the paired tool_use, got %r' % (r['tool'],)
        assert r['reason'] == 'classifier_no_verdict', 'reason: got %r' % (r['reason'],)
        assert r['consecutive_denials'] == 1, 'consecutive_denials: got %r' % (r['consecutive_denials'],)
        assert r['intended_command'] == CMD, 'intended_command must be the whole command of the paired tool_use'


def test_the_intended_command_is_never_truncated():
    assert len(CMD) > 300, 'the fixture command must be long enough to expose a slice'
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        error_scan.record_denials([use(1), result(1)], ledger=led)
        assert ledger_rows(led)[0]['intended_command'] == CMD, \
            'intended_command was cut: a recorded value is never sliced'


def test_a_run_of_denials_counts_up_in_order():
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        rows = [use(1), result(1, ts='2026-09-29T15:00:38.177Z'),
                use(2), result(2, ts='2026-09-29T15:00:44.524Z'),
                use(3), result(3, ts='2026-09-29T15:01:03.571Z')]
        assert error_scan.record_denials(rows, ledger=led) == 3, 'three denials write three rows'
        got = [r['consecutive_denials'] for r in ledger_rows(led)]
        assert got == [1, 2, 3], 'a run of three denials counts 1, 2, 3 -- got %r' % (got,)


def test_a_successful_tool_result_resets_the_run():
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        rows = [use(1), result(1, ts='2026-09-29T15:00:38.177Z'),
                use(2), result(2, ts='2026-09-29T15:00:44.524Z'),
                use(3, name='Read'), result(3, text='120 lines read', is_error=False,
                                            ts='2026-09-29T15:00:47.000Z'),
                use(4), result(4, ts='2026-09-29T15:00:58.560Z')]
        assert error_scan.record_denials(rows, ledger=led) == 3, \
            'the successful tool result writes no row; the three denials do'
        got = [r['consecutive_denials'] for r in ledger_rows(led)]
        assert got == [1, 2, 1], 'the success between denial 2 and 3 resets the run -- got %r' % (got,)


def test_the_ledger_keeps_the_two_commands_distinct():
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        second = 'python3 scripts/library.py register scripts/tests/test_coder_branch_delivery.py'
        rows = [use(1), result(1), use(2, cmd=second), result(2)]
        error_scan.record_denials(rows, ledger=led)
        got = [r['intended_command'] for r in ledger_rows(led)]
        assert got == [CMD, second], 'each row carries the command of ITS OWN tool_use -- got %r' % (got,)


def test_an_unrelated_failure_is_not_a_classifier_denial():
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        n = error_scan.record_denials([use(1), result(1, text=UNRELATED)], ledger=led)
        assert n == 0, 'a tool failure with no classifier verdict writes nothing, got %r' % (n,)
        assert not Path(led).exists(), 'a scan with no denial must not create a ledger file'


def test_the_phrase_in_a_successful_tool_result_writes_nothing():
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        n = error_scan.record_denials([use(1), result(1, is_error=False)], ledger=led)
        assert n == 0, 'the phrase in a successful tool result is prose, not a denial: got %r' % (n,)
        assert not Path(led).exists(), 'a scan with no denial must not create a ledger file'


def test_scan_writes_the_ledger_through_the_real_entry_point():
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        out = error_scan.scan([transcript(td)], ledger=led)
        rows = ledger_rows(led)
        assert len(rows) == 1, 'scan() must append the denial row itself, got %d rows' % len(rows)
        assert rows[0]['tool'] == 'Bash', 'row tool: got %r' % (rows[0]['tool'],)
        assert rows[0]['intended_command'] == CMD, 'the row scan() wrote is not the denial\'s command'
        assert rows[0]['ts'] == TS, 'row ts: got %r' % (rows[0]['ts'],)
        kinds = [f['kind'] for f in out]
        assert 'transcript-failure' in kinds, \
            'scan() must still produce its transcript-failure finding for this row: %r' % (kinds,)


def test_the_finding_contract_still_holds_after_the_change():
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        out = error_scan.scan([transcript(td)], ledger=led)
        one = [f for f in out if f['kind'] == 'transcript-failure']
        assert len(one) == 1, 'expected exactly one transcript-failure finding, got %d' % len(one)
        assert set(one[0]) == {'kind', 'source', 'locator', 'evidence', 'at'}, \
            'the finding contract\'s five keys changed: %s' % sorted(one[0])
        assert one[0]['evidence'] == json.dumps(
            {'type': 'user', 'sessionId': SID, 'timestamp': TS, 'uuid': 'r1',
             'sourceToolAssistantUUID': 'a1',
             'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'toolu-1',
                                      'is_error': True, 'content': DENIAL}]}}), \
            'the evidence must be the whole row, not a prefix of it'


def test_a_second_scan_of_the_same_transcript_does_not_double_count():
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        f = transcript(td)
        error_scan.scan([f], ledger=led)
        error_scan.scan([f], ledger=led)
        assert len(ledger_rows(led)) == 1, \
            're-scanning the same transcript must not record the same denial twice'


def test_an_unreadable_ledger_row_is_reported_and_not_swallowed():
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / 'denials.jsonl'
        led.write_text('this line is not json\n', encoding='utf-8')
        err = StringIO()
        with redirect_stderr(err):
            error_scan.scan([transcript(td)], ledger=str(led))
        body = led.read_text(encoding='utf-8').splitlines()
        assert body[0] == 'this line is not json', 'an unreadable row must be kept, not rewritten away'
        assert len(body) == 2, 'the new denial must still be appended after it -- got %r' % (body,)
        assert 'unreadable' in err.getvalue(), \
            'the unreadable row must be reported on stderr, not swallowed: %r' % (err.getvalue(),)


def test_the_top_level_tool_result_shape_is_counted():
    top = {'type': 'tool_result', 'is_error': True, 'content': DENIAL,
           'sessionId': SID, 'timestamp': TS}
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        assert error_scan.record_denials([use(1), top], ledger=led) == 1, \
            'a top-level tool_result row must be counted, not only the nested shape'
        r = ledger_rows(led)[0]
        assert r['reason'] == 'classifier_no_verdict', 'reason: got %r' % (r['reason'],)
        assert r['ts'] == TS, 'ts: got %r' % (r['ts'],)
        assert r['session'] == SID, 'session: got %r' % (r['session'],)
        assert isinstance(r['intended_command'], str) and r['intended_command'], \
            'an unpaired denial is still recorded, with a string intended_command'
        assert isinstance(r['tool'], str) and r['tool'], \
            'an unpaired denial is still recorded, with a string tool'


def test_two_sessions_count_their_runs_separately():
    with tempfile.TemporaryDirectory() as td:
        led = str(Path(td) / 'denials.jsonl')
        rows = [use(1, sid=SID), use(2, sid=SID2),
                result(1, ts='2026-09-29T15:00:38.177Z', sid=SID),
                result(2, ts='2026-09-29T15:00:44.524Z', sid=SID2)]
        assert error_scan.record_denials(rows, ledger=led) == 2, 'two sessions, two rows'
        got = {r['session']: r['consecutive_denials'] for r in ledger_rows(led)}
        assert got == {SID: 1, SID2: 1}, 'each session has its own run -- got %r' % (got,)


if __name__ == '__main__':
    tests = [f for name, f in sorted(globals().items())
             if name.startswith('test_') and callable(f) and f.__name__.startswith('test_')]
    failed = 0
    for t in tests:
        try:
            t()
            print('ok   %s' % t.__name__)
        except AssertionError as exc:
            failed += 1
            print('FAIL %s: %s' % (t.__name__, exc))
        except Exception as exc:
            failed += 1
            print('ERROR %s: %s: %s' % (t.__name__, type(exc).__name__, exc))
    print('%d/%d passed' % (len(tests) - failed, len(tests)))
    sys.exit(1 if failed else 0)
