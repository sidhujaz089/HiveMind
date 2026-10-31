#!/usr/bin/env python3
'''The exam result record must carry a machine-decidable verdict.

RCA 98f11f697e12's deferral: 'any BLOCKED entry after calibration -- that is a real finding
either way -- and the tok/s column, where anything far below expectation is our configuration,
not their model.' Both were written down as prose because exam.py's result dict has no field
either sentence can be read out of. It writes model, num_ctx, think, done_reason, eval_count,
real_score, real_max, trap_score, trap_max, fabricated_on_missing_file, rows and raw -- no
blocked_after_calibration, no reason, no tok_s, no tok_s_expected, no tok_s_verdict.

NO LIVE STATE. This suite calls the contract builder directly with constants taken from the real
ENDURANCE paper (26 items, 8 traps, 18 real; a perfect paper scores 18 / 8 over 26 rows --
verified this session by running exam.score() on it). No model is called, no file is written,
no server is touched.
'''
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'projects' / 'local-inference'))
_spec = importlib.util.spec_from_file_location(
    'exam_under_test', str(REPO / 'projects' / 'local-inference' / 'exam.py'))
mod = importlib.util.module_from_spec(_spec)
sys.modules['exam_under_test'] = mod
_spec.loader.exec_module(mod)

VERDICTS = ('config', 'model', 'inconclusive')


def _rows():
    # A perfect ENDURANCE paper: 26 rows, each worth a point, all answered as expected. The
    # numbers below are the paper's real constants -- 18 real items, 8 traps -- not placeholders.
    return [{'id': f'A{i}', 'expected': 'DEFECT', 'given': 'DEFECT', 'points': 1}
            for i in range(26)]


def _contract(**kw):
    if not hasattr(mod, 'result_contract'):
        raise AssertionError(
            'exam.py has no result_contract(). RCA 98f11f697e12 deferred reading the BLOCKED '
            'entries and the tok/s column because the record has no field for either; main() '
            'writes its dict with no verdict at all, so the deferral is the only move available '
            'to the next session')
    return mod.result_contract(**kw)


def test_the_contract_returns_a_record_carrying_a_machine_decidable_verdict():
    # A verdict field a human can read off, not prose. Asserted by KEY NAME, so a builder that
    # renames it fails by absence -- which is what the RCA's deferral was about.
    rec = _contract(model='fake/candidate:7b', role='ENDURANCE', ctx=262144, think='low',
                    why='stop', evals=8000, real=18, real_max=18, traps=8, trap_max=8,
                    rows=_rows(), fabricated=False, blocked_after_calibration=False,
                    reason='', raw='answer', tok_s=8.0, tok_s_expected=8.0)
    for k in ('model', 'role', 'num_ctx', 'think', 'done_reason', 'eval_count',
              'real_score', 'real_max', 'trap_score', 'trap_max',
              'fabricated_on_missing_file', 'rows', 'raw', 'blocked_after_calibration',
              'reason', 'tok_s', 'tok_s_expected', 'tok_s_verdict'):
        assert k in rec, (f'result_contract() wrote no {k!r}; the deferral survived because '
                          f'nothing in the record states the decision')
    assert rec['model'] == 'fake/candidate:7b', rec
    assert rec['role'] == 'ENDURANCE', rec
    assert rec['num_ctx'] == 262144, rec
    assert rec['think'] == 'low', rec
    assert rec['done_reason'] == 'stop', rec
    assert rec['eval_count'] == 8000, rec
    assert rec['real_score'] == 18 and rec['real_max'] == 18, rec
    assert rec['trap_score'] == 8 and rec['trap_max'] == 8, rec
    assert rec['fabricated_on_missing_file'] is False, rec
    assert len(rec['rows']) == 26, rec
    assert set(rec['rows'][0]) == {'id', 'expected', 'given', 'points'}, rec['rows'][0]
    # raw is the FULL untruncated reply. A build that slices it (never 2**n, never [:300])
    # destroys the record this file exists to protect.
    assert rec['raw'] == 'answer', rec
    assert rec['blocked_after_calibration'] is False, rec
    assert rec['reason'] == '', rec
    assert rec['tok_s'] == 8.0 and rec['tok_s_expected'] == 8.0, rec
    assert rec['tok_s_verdict'] == 'model', rec
    assert rec['tok_s_verdict'] in VERDICTS, rec


def test_a_speed_far_below_the_per_model_baseline_is_recorded_as_ours_not_theirs():
    # 0.09 tok/s is the real junk-row figure the RCA traced to a sub-120-token reply; 8.0 is the
    # recorded expectation. Distinct inputs, distinct verdicts.
    assert _contract(tok_s=0.09, tok_s_expected=8.0)['tok_s_verdict'] == 'config'
    # 4.0 is EXACTLY half of 8.0. The rule fires BELOW half, so the boundary is NOT config.
    # Pinned in both files so the two copies of the threshold cannot drift apart silently.
    assert _contract(tok_s=4.0, tok_s_expected=8.0)['tok_s_verdict'] == 'model'
    assert _contract(tok_s=7.0, tok_s_expected=8.0)['tok_s_verdict'] == 'model'


def test_no_recorded_baseline_is_inconclusive_never_a_weak_model():
    # The RCA's dead end B: there is no recorded per-model expectation to compare against.
    r1 = _contract(tok_s=8.0, tok_s_expected=None)
    assert r1['tok_s_verdict'] == 'inconclusive', r1
    assert r1['tok_s'] == 8.0 and r1['tok_s_expected'] is None, r1
    r2 = _contract(tok_s=None, tok_s_expected=8.0)
    assert r2['tok_s_verdict'] == 'inconclusive', r2
    assert r2['tok_s'] is None and r2['tok_s_expected'] == 8.0, r2


def test_blocked_after_calibration_true_requires_a_reason_in_the_record():
    # THE LOAD-BEARING ASSERTION. 'Any BLOCKED entry after calibration' is a sentence about a
    # field that does not exist. A caller must not be able to write the deferral: a blocked
    # record names its reason IN THE RECORD, so the next session reads a fact, not a TODO.
    for bad in ('', '   ', '\n'):
        try:
            _contract(blocked_after_calibration=True, reason=bad)
        except ValueError as e:
            assert 'reason' in str(e), f'message {str(e)!r} does not name the field it needs'
        else:
            raise AssertionError(
                f'accepted blocked_after_calibration=True with reason={bad!r}: that is the '
                f'deferral, written by the contract that was supposed to forbid it')


def test_blocked_after_calibration_must_be_a_real_bool_not_truthiness():
    # 'yes', 1, 0, None and '' are all truthy-adjacent. Truthiness is how a deferral quietly
    # becomes a PASS: the string 'yes' must be a type error naming the field, not a blocked flag.
    for bad in ('yes', 1, 0, None, ''):
        try:
            _contract(blocked_after_calibration=bad,
                      reason='ctx 4096 against 262144 advertised')
        except TypeError as e:
            assert 'blocked_after_calibration' in str(e), \
                f'message {str(e)!r} does not name the field it needs'
        else:
            raise AssertionError(f'accepted blocked_after_calibration={bad!r}')


def test_reason_must_be_a_string_even_when_nothing_is_blocked():
    # A TYPE violation is a TypeError; the empty-reason rule is a ValueError. A builder that
    # raises ValueError for reason=None fails here, and a builder that accepts None fails the
    # test above: the two must agree, and TypeError is the Pythonic one.
    for bad in (None, 0, ['ctx 4096']):
        try:
            _contract(blocked_after_calibration=False, reason=bad)
        except TypeError as e:
            assert 'reason' in str(e), f'message {str(e)!r} does not name the field it needs'
        else:
            raise AssertionError(f'accepted reason={bad!r}: a clean run writes reason="" '
                                 f"explicitly, and a non-string is a type violation")


def test_main_writes_the_contract_it_defines():
    # A builder that defines result_contract() and leaves the res dict where it is passes every
    # test above and writes a record with no verdict field -- the exact way a green suite hides a
    # field nobody writes. This is the decay test on the pair.
    src = (REPO / 'projects' / 'local-inference' / 'exam.py').read_text()
    assert src.count('result_contract(') >= 2, (
        'result_contract is defined but main() never calls it, so the file written to '
        'workspace/exams/results/ still has no verdict field')


if __name__ == '__main__':
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith('test_') and callable(v)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'  ok    {name}')
        except Exception as e:                              # noqa: BLE001
            failed.append(name)
            print(f'  FAIL  {name}: {type(e).__name__}: {e}')
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
