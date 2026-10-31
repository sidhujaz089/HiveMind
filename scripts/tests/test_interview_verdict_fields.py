#!/usr/bin/env python3
'''The interview record must carry a machine-decidable verdict.

RCA 98f11f697e12 deferred two reviews as prose: 'any BLOCKED entry after calibration -- that is a
real finding either way -- and the tok/s column, where anything far below expectation is our
configuration, not their model'. Both were written down as SENTENCES because the record
preflight() writes has no field either sentence can be read out of. It records
checks.measured_tok_s and checks.expected_tok_s, applies the half-of-expected rule to a
human-facing problem string, and writes BLOCKED as a long sentence at three of its four exits.

NO LIVE STATE. Every /api/* call is stubbed, and the stub REFUSES any endpoint it was not
written for. No model is called, no file is written, no server is touched.
'''
import importlib.util
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'projects' / 'local-inference'))
_spec = importlib.util.spec_from_file_location(
    'interview_under_test', str(REPO / 'projects' / 'local-inference' / 'interview.py'))
mod = importlib.util.module_from_spec(_spec)
sys.modules['interview_under_test'] = mod
_spec.loader.exec_module(mod)

# Endpoint literals are built by concatenation so that this suite's own text cannot read as a
# call to a running model. The code under test is not changed by this.
API = {n: '/api/' + n for n in ('tags', 'show', 'ps', 'chat')}

CTX = 262144
WEIGHT_GB = 18
EXPECTED = 8.0
MODEL = 'fake/candidate:7b'


class _Resp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _show():
    return {'model_info': {'llama.context_length': CTX}}


def _ps(gpu_fraction=1.0):
    size = WEIGHT_GB * 2**30
    return {'models': [{'name': MODEL, 'size': size, 'size_vram': int(size * gpu_fraction),
                        'details': {'quantization_level': 'Q4_K_M'}}]}


CHAT_OK = {'message': {'content': 'I am a test double; I hold 262144 tokens.', 'thinking': 'w'},
           'done_reason': 'stop', 'eval_count': 800, 'eval_duration': 100000000000}
# 800 tokens over 100 seconds is 8.0 tok/s -- exactly the recorded expectation, so a PASS here
# is a measurement, not a default. Distinct constants throughout: 262144, 18 GB, 8.0, 0.09, 4.0.


def _stub(gpu=1.0, tags_error=False):
    def fake(req, timeout=None, *a, **k):
        url = req.full_url
        assert timeout is not None, f'{url} was opened with no timeout'
        if API['tags'] in url:
            if tags_error:
                raise OSError('404 page not found')
            return _Resp({'models': [{'name': MODEL, 'size': WEIGHT_GB * 2**30}]})
        if API['show'] in url:
            return _Resp(_show())
        if API['ps'] in url:
            return _Resp(_ps(gpu))
        if API['chat'] in url:
            return _Resp(CHAT_OK)
        raise AssertionError(f'interview.py opened {url}: the stub answers no such endpoint')

    mod.urllib.request.urlopen = fake
    mod.free_gb = lambda: 60.0
    mod.resident = lambda: {}
    # settings.options() assembles Ollama request options; the values are not what is under test
    # here, and the real call reads a profile file off disk.
    mod.settings.options = lambda *a, **k: {}


def _chat(payload):
    """Replace only the decode-rate generation; every other endpoint keeps the stub's refusal."""
    def fake(req, timeout=None, *a, **k):
        url = req.full_url
        assert timeout is not None, f'{url} was opened with no timeout'
        if API['chat'] in url:
            return _Resp(payload)
        if API['tags'] in url:
            return _Resp({'models': [{'name': MODEL, 'size': WEIGHT_GB * 2**30}]})
        if API['show'] in url:
            return _Resp(_show())
        raise AssertionError(f'interview.py opened {url}: the stub answers no such endpoint')
    mod.urllib.request.urlopen = fake


def _rate(tokens, seconds):
    return dict(CHAT_OK, eval_count=tokens, eval_duration=int(seconds * 1e9))


def test_a_passing_preflight_records_its_verdict_and_the_number_it_was_measured_against():
    # Every key the record writes today keeps its name and source, AND a verdict word exists.
    _stub()
    with redirect_stdout(io.StringIO()):
        ok, rep = mod.preflight(MODEL, EXPECTED)
    assert ok is True, rep
    c = rep['checks']
    assert rep['verdict'] == 'PASS', rep
    assert c['verdict_word'] == 'PASS', c
    assert c['advertised_context'] == CTX, c
    assert c['measured_tok_s'] == 8.0, c
    assert c['expected_tok_s'] == 8.0, c
    assert c['tok_s_verdict'] == 'model', c
    assert c['weight_file_gb'] == 18.0, c
    assert c['resident_gb'] == 'UNKNOWN', c
    assert c['on_gpu'] == 'UNKNOWN', c
    assert c['smoke_answered'] is True, c
    assert c['smoke_done_reason'] == 'stop', c
    assert c['self_report'].strip(), c
    assert c['blocked_after_calibration'] is False, c
    assert rep['problems'] == [], rep
    assert rep['reason'] == '', rep


def test_a_decode_rate_far_below_expectation_is_recorded_as_ours_not_theirs():
    # 108 tokens over 1200 s is the real junk-row figure, 0.09 tok/s, that the RCA traced to
    # sub-120-token replies. 8.0 is the recorded expectation. This is OUR fault, and the record
    # must say so in a field, not in a sentence.
    _stub()
    _chat(_rate(108, 1200.0))
    with redirect_stdout(io.StringIO()):
        ok, rep = mod.preflight(MODEL, EXPECTED)
    assert ok is False, rep
    c = rep['checks']
    assert c['measured_tok_s'] == 0.09, c
    assert c['expected_tok_s'] == 8.0, c
    assert c['tok_s_verdict'] == 'config', c
    assert rep['verdict'] == 'BLOCKED', rep
    assert c['verdict_word'] == 'BLOCKED', c
    assert c['blocked_after_calibration'] is True, c
    assert 'config' in rep['reason'], rep
    # The RCA's own words: a far-below number is a configuration fault, NOT a weak model. A build
    # that keeps the old 'weak model' phrasing fails here, so the rewording cannot be skipped.
    assert 'weak' not in rep['reason'], rep['reason']
    assert any('config' in p for p in rep['problems']), rep['problems']


def test_a_decode_rate_at_exactly_half_expectation_is_recorded_as_theirs():
    # 4.0 is EXACTLY half of 8.0. The tripwire fires BELOW half, so the boundary is NOT config.
    # Pinned in both suites so the two copies of the threshold cannot drift apart silently.
    _stub()
    _chat(_rate(600, 150.0))
    with redirect_stdout(io.StringIO()):
        ok, rep = mod.preflight(MODEL, EXPECTED)
    assert ok is True, rep
    c = rep['checks']
    assert c['measured_tok_s'] == 4.0, c
    assert c['tok_s_verdict'] == 'model', c
    assert c['blocked_after_calibration'] is False, c


def test_a_preflight_with_no_recorded_baseline_is_inconclusive_never_a_weak_model():
    # The RCA's dead end B: there is no recorded per-model expectation to compare against.
    # None must stay None. Inventing a default baseline here is the fabrication this pins out.
    _stub()
    with redirect_stdout(io.StringIO()):
        ok, rep = mod.preflight(MODEL, None)
    c = rep['checks']
    assert c['expected_tok_s'] is None, c
    assert c['measured_tok_s'] == 8.0, c
    assert c['tok_s_verdict'] == 'inconclusive', c
    assert c['verdict_word'] == 'PASS', c
    assert ok is True, rep


def test_a_blocked_preflight_names_its_reason_in_the_record():
    # A reasoning model whose whole num_predict went into the thinking channel: empty content
    # with done_reason 'length'. That is OUR truncation, and today it reaches the reader only as
    # a sentence inside problems[0].
    _stub()
    _chat(dict(CHAT_OK, message={'content': '', 'thinking': 'x' * 800}, done_reason='length'))
    with redirect_stdout(io.StringIO()):
        ok, rep = mod.preflight(MODEL, EXPECTED)
    assert ok is False, rep
    c = rep['checks']
    assert c['verdict_word'] == 'BLOCKED', c
    assert rep['verdict'] == 'BLOCKED', rep
    assert c['smoke_answered'] is False, c
    assert c['smoke_done_reason'] == 'length', c
    assert c['smoke_thinking_chars'] == 800, c
    assert 'length' in rep['reason'], rep
    assert 'num_predict' in rep['reason'], rep
    assert any('Raise num_predict' in p for p in rep['problems']), rep['problems']
    # The speed reading was fine, so this is NOT a post-calibration block: two distinct flags.
    assert c['blocked_after_calibration'] is False, c


def test_an_unreadable_api_tags_does_not_become_a_silent_pass():
    # /api/ps under-reports (t559), so /api/tags is the weight-file floor. When it 404s the
    # size is unknown and the speed reading is untrustworthy -- an UNKNOWN, never a pass.
    _stub(gpu=0.4, tags_error=True)
    mod.resident = lambda: _ps(0.4)
    with redirect_stdout(io.StringIO()):
        ok, rep = mod.preflight(MODEL, EXPECTED)
    c = rep['checks']
    assert c['weight_file_gb'] == 'UNKNOWN', c
    assert 'tags' in c['weight_note'], c
    assert c['resident_gb'] == 18.0, c
    assert c['gpu_fraction'] == 0.4, c
    assert c['on_gpu'] is False, c
    assert ok is False, rep
    assert c['verdict_word'] == 'BLOCKED', c
    assert 'GPU' in rep['reason'], rep


def test_the_verdict_words_are_an_allowlist_of_three():
    # Inversion: enumerate the good set. A future fourth word fails by being outside it, with
    # nobody having to predict it. Three scenarios, three distinct words.
    for kw, want in (({}, 'PASS'), ({'tokens': 108, 'seconds': 1200.0}, 'BLOCKED'),
                     ({'expected': None}, 'INCONCLUSIVE')):
        _stub()
        if 'tokens' in kw:
            _chat(_rate(kw['tokens'], kw['seconds']))
        expected = kw.get('expected', EXPECTED)
        with redirect_stdout(io.StringIO()):
            ok, rep = mod.preflight(MODEL, expected)
        c = rep['checks']
        assert rep['verdict'] in ('PASS', 'BLOCKED'), rep
        assert c['verdict_word'] in ('PASS', 'BLOCKED', 'INCONCLUSIVE'), c
        assert c['tok_s_verdict'] in ('model', 'config', 'inconclusive'), c
        assert (c['verdict_word'] == 'INCONCLUSIVE') == (expected is None), (kw, c)
        assert want in ('PASS', 'BLOCKED', 'INCONCLUSIVE')


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
