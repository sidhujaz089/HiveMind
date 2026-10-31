#!/usr/bin/env python3
'''t779 oracle: a row at the endpoint that produces a model proposal.

workspace/rca/2fc99214d166.md: a model was asked to propose, the proposal went
into the transcript, and nothing on disk recorded the proposal, its outcome, the
task it landed through, or its live state -- nine days later its live state was
unprovable (Rule 4 case 2, dead end D1). This fixture is the recorder's
proof-of-life: it is red while scripts/proposals.py is absent.

Run: python3 scripts/tests/test_proposals_writer.py
'''
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / 'scripts' / 'proposals.py'

REQUIRED = {'at', 'session', 'key', 'quote', 'model', 'proposed_by',
            'outcome', 'lands_via', 'live_state'}

SESSION = '6cc34e3b-0c16-4f3b-8f78-98893304830f'
KEY = '2fc99214d166'
PROPOSE_A = ('Qwen proposes we capture the same data the coding path records, '
             'as a row written at the endpoint that produces the proposal.')
PROPOSE_B = 'Gemma proposed a ledger row per proposal, keyed by the finding key.'
NOT_A_PROPOSAL = 'Qwen returned 42 tokens with done_reason=stop and no next step.'


def load_proposals():
    spec = importlib.util.spec_from_file_location('proposals_under_test', MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(
            'scripts/proposals.py is not importable from ' + str(MODULE_PATH)
            + ' -- the proposal endpoint has no writer, which is the defect this '
            'fixture exists to fail on (workspace/rca/2fc99214d166.md, dead end D1)')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rows_of(path):
    if not path.is_file():
        return []
    return [json.loads(line)
            for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def test_the_endpoint_module_defines_the_writer_and_its_vocabulary():
    mod = load_proposals()
    for name in ('record_proposal', 'find_proposals'):
        assert callable(getattr(mod, name, None)), (
            'scripts/proposals.py defines no callable ' + name +
            '() -- the endpoint has no writer and no detector')
    assert set(mod.OUTCOMES) == {'built', 'proposed-only', 'declined'}, (
        'OUTCOMES is ' + repr(mod.OUTCOMES) + ', not the three outcomes t779 names')


def test_a_proposal_line_is_found_and_prose_is_not():
    mod = load_proposals()
    text = '\n'.join([NOT_A_PROPOSAL, PROPOSE_A, 'nothing to see here', PROPOSE_B])
    hits = mod.find_proposals(text)
    assert isinstance(hits, list), 'find_proposals returned ' + type(hits).__name__
    # Source order, verbatim, and the non-proposal lines dropped.
    assert [h['quote'] for h in hits] == [PROPOSE_A, PROPOSE_B], (
        'find_proposals gave ' + repr([h.get('quote') for h in hits]) +
        ' -- it must return every line naming a proposal, in source order, '
        'unmodified, and no line that does not')
    for hit in hits:
        assert set(hit) == {'quote'}, (
            'a find_proposals hit carries ' + repr(sorted(hit)) +
            ', expected exactly the key quote')
    assert mod.find_proposals(NOT_A_PROPOSAL) == [], (
        'a line naming no proposal was returned -- the detector matches prose')
    assert mod.find_proposals('') == [], 'empty text yielded a proposal'


def test_a_long_proposal_is_recorded_whole_not_sliced():
    # never-truncate-a-record: a proposal is a record; the quote is the finding.
    mod = load_proposals()
    long_line = 'Qwen proposes ' + ('a durable row per proposal ' * 120)
    hits = mod.find_proposals('header line\n' + long_line)
    assert [h['quote'] for h in hits] == [long_line], (
        'a proposal of ' + str(len(long_line)) + ' characters was cut: the recorded '
        'quote is ' + str(len(hits[0]['quote'])) + ' characters')
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        row = mod.record_proposal(session=SESSION, key=KEY, quote=long_line,
                                  model='qwen3.8:27b', proposed_by='User',
                                  outcome='proposed-only',
                                  live_state='in-transcript-only', repo=repo)
        assert row['quote'] == long_line, (
            'record_proposal truncated the quote: ' + str(len(row['quote'])) +
            ' of ' + str(len(long_line)) + ' characters')


def test_record_proposal_writes_the_nine_named_fields():
    mod = load_proposals()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        index = repo / 'workspace' / 'proposals.jsonl'
        row = mod.record_proposal(session=SESSION, key=KEY, quote=PROPOSE_A,
                                  model='qwen3.8:27b', proposed_by='User',
                                  outcome='proposed-only', lands_via='',
                                  live_state='in-transcript-only', repo=repo)
        assert isinstance(row, dict), 'record_proposal returned ' + type(row).__name__
        assert set(row) == REQUIRED, (
            'row keys ' + repr(sorted(row)) + ' are not the nine fields t779 names: '
            + repr(sorted(REQUIRED)))
        assert row['session'] == SESSION, 'row session is ' + repr(row.get('session'))
        assert row['key'] == KEY, 'row key is ' + repr(row.get('key'))
        assert row['quote'] == PROPOSE_A, 'row quote is not the proposal verbatim'
        assert row['model'] == 'qwen3.8:27b', 'row model is ' + repr(row.get('model'))
        assert row['proposed_by'] == 'User', 'row proposed_by is ' + repr(row.get('proposed_by'))
        assert row['outcome'] == 'proposed-only', 'row outcome is ' + repr(row.get('outcome'))
        assert row['lands_via'] == '', 'row lands_via is ' + repr(row.get('lands_via'))
        assert row['live_state'] == 'in-transcript-only', (
            'row live_state is ' + repr(row.get('live_state')))
        assert str(row['at']).endswith('Z'), 'row at is not a UTC Z stamp: ' + repr(row.get('at'))
        written = rows_of(index)
        assert len(written) == 1, (
            'expected 1 row in workspace/proposals.jsonl, found ' + str(len(written)) +
            ' -- the default ledger of the writer must be <repo>/workspace/proposals.jsonl')
        assert written[0] == row, (
            'the row returned is not the row appended to the ledger: '
            + repr(sorted(set(written[0]) ^ set(row))))


def test_the_ledger_is_appended_not_rewritten():
    mod = load_proposals()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        index = repo / 'workspace' / 'proposals.jsonl'
        index.parent.mkdir(parents=True)
        first = {'at': '2026-10-07T23:05:06Z', 'session': 'earlier-session',
                 'key': '3d9cf41e0896', 'quote': 'an earlier proposal',
                 'model': 'qwen3.8:27b', 'proposed_by': 'Assistant',
                 'outcome': 'declined', 'lands_via': '', 'live_state': 'not-built'}
        index.write_text(json.dumps(first) + '\n', encoding='utf-8')
        mod.record_proposal(session=SESSION, key=KEY, quote=PROPOSE_B,
                            model='gemma4:26b', proposed_by='User',
                            outcome='built', lands_via='t779',
                            live_state='merged', repo=repo)
        written = rows_of(index)
        assert len(written) == 2, (
            'expected 2 rows after appending to an existing ledger, found ' + str(len(written))
            + ' -- a proposal ledger that rewrites drops every earlier proposal')
        assert written[0] == first, 'the pre-existing proposal row was modified'
        assert written[1]['key'] == KEY, 'the new row is not last: ' + repr(written[-1].get('key'))
        assert written[1]['model'] == 'gemma4:26b', (
            'model leaked from the pre-existing row: ' + repr(written[1].get('model')))


def test_a_quote_is_written_once_per_key():
    mod = load_proposals()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        first = mod.record_proposal(session=SESSION, key=KEY, quote=PROPOSE_A,
                                    model='qwen3.8:27b', proposed_by='User',
                                    outcome='proposed-only',
                                    live_state='in-transcript-only', repo=repo)
        second = mod.record_proposal(session=SESSION, key=KEY, quote=PROPOSE_A,
                                     model='qwen3.8:27b', proposed_by='User',
                                     outcome='proposed-only',
                                     live_state='in-transcript-only', repo=repo)
        written = rows_of(repo / 'workspace' / 'proposals.jsonl')
        assert len(written) == 1, (
            str(len(written)) + ' rows for one key: the task requires a quote be '
            'written once, keyed by finding key')
        assert first == second, (
            'the second call for a key returned a different row: '
            + repr(sorted(set(first) ^ set(second))))


def test_an_outcome_outside_the_vocabulary_is_refused_and_writes_nothing():
    mod = load_proposals()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        index = repo / 'workspace' / 'proposals.jsonl'
        try:
            mod.record_proposal(session=SESSION, key=KEY, quote=PROPOSE_A,
                                model='qwen3.8:27b', proposed_by='User',
                                outcome='maybe', live_state='unknown', repo=repo)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "outcome 'maybe' was accepted: an outcome outside "
                + repr(sorted(mod.OUTCOMES)) + ' makes the ledger unspeakable')
        assert not index.exists(), 'a refused proposal left a row in the ledger'


def test_a_built_proposal_must_name_how_it_landed():
    # rca/2fc99214d166.md: a proposal with no durable object behind it.
    mod = load_proposals()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        index = repo / 'workspace' / 'proposals.jsonl'
        try:
            mod.record_proposal(session=SESSION, key=KEY, quote=PROPOSE_A,
                                model='qwen3.8:27b', proposed_by='User',
                                outcome='built', lands_via='',
                                live_state='merged', repo=repo)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "outcome 'built' was accepted with lands_via empty: 'built' with no "
                'tNNN/commit is the unprovable state this task exists to stop')
        assert not index.exists(), 'a refused proposal left a row in the ledger'


def test_a_proposed_only_proposal_must_state_its_live_state():
    mod = load_proposals()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        index = repo / 'workspace' / 'proposals.jsonl'
        try:
            mod.record_proposal(session=SESSION, key=KEY, quote=PROPOSE_A,
                                model='qwen3.8:27b', proposed_by='User',
                                outcome='proposed-only', lands_via='',
                                live_state='', repo=repo)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "outcome 'proposed-only' was accepted with live_state empty: nine "
                "days on, 'is it live?' is the question with no answer on disk")
        assert not index.exists(), 'a refused proposal left a row in the ledger'


def test_a_proposal_without_a_key_is_refused():
    # key is the dedup key; a keyless row cannot be written-once.
    mod = load_proposals()
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        index = repo / 'workspace' / 'proposals.jsonl'
        try:
            mod.record_proposal(session=SESSION, key='', quote=PROPOSE_A,
                                model='qwen3.8:27b', proposed_by='User',
                                outcome='declined', live_state='not-built', repo=repo)
        except ValueError:
            pass
        else:
            raise AssertionError(
                "an empty key was accepted: rows keyed by '' collide, and the "
                'finding key is what makes a quote written once')
        assert not index.exists(), 'a refused proposal left a row in the ledger'


def test_the_endpoint_writes_to_workspace_proposals_jsonl():
    mod = load_proposals()
    name = Path(str(mod.OUT)).name
    assert name == 'proposals.jsonl', (
        "the default ledger of the writer is " + repr(mod.OUT) + "; the task names "
        "the endpoint file workspace/proposals.jsonl (t779 item 1)")


def run_all():
    tests = sorted((name, fn) for name, fn in globals().items()
                   if name.startswith('test_') and callable(fn))
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('PASS ' + name)
        except BaseException as exc:            # noqa: BLE001 - report, never swallow
            failed += 1
            # str(exc), never exc: concatenating a str with an exception object raises
            # TypeError and the report is lost (verified in test_askmemory_proposal_
            # lands_in_repo.py:172, which dies this way on its first failure).
            print('FAIL ' + name + ': ' + type(exc).__name__ + ': ' + str(exc))
    print('')
    print(str(len(tests) - failed) + '/' + str(len(tests)) + ' passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(run_all())
