#!/usr/bin/env python3
"""t1241 -- coder.py ask() records every model call to workspace/model-calls.jsonl.

WHY (RCA d43cb013caf7, C2/D1): the run ledger records a dispatch only on a TERMINAL outcome,
so a model call that HANGS leaves no row at all and 'is the batch alive?' is unanswerable
while it runs. ask() must append a STARTED row BEFORE it opens the HTTP request and a
COMPLETED row after the reply is parsed, paired by call_id -- STARTED-without-COMPLETED is
the signature of a hang.

No model, no server, no subprocess, no process left running. The transport is a fake that
answers from memory, the telemetry writer is stubbed (a fake transport alone does NOT stop
run_telemetry.record from appending to the repo's run-telemetry.jsonl -- that is exactly what
made the previous version of this suite dirty), every destination is a file under a fresh
tempfile.mkdtemp() dir, and the repo's own model-calls.jsonl is asserted byte-for-byte clean.
"""
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'projects' / 'local-inference' / 'coder.py'
LIVE = ROOT / 'workspace' / 'model-calls.jsonl'
TELE = ROOT / 'workspace' / 'run-telemetry.jsonl'
MODEL = 'qwen3.6-test'
MAX_TOKENS = 4321
MAX_TOKENS_B = 5321
TIMEOUT_SECS = 1234
PAYLOAD = {'choices': [{'message': {'role': 'assistant', 'content': 'ANSWER',
                                    'thinking': 'THINKING'},
                        'finish_reason': 'stop'}],
           'usage': {'completion_tokens': 7, 'prompt_tokens': 11}}
fails = []
seen = []
tel = []
DEST = None
DEST2 = None
coder = None


def check(name, cond, extra=''):
    print(('PASS  ' if cond else 'FAIL  ') + name + ('  [' + extra + ']' if extra else ''))
    if not cond:
        fails.append(name)
    return cond


def rows_of(path):
    """Every whole JSON row in the file. A torn line raises -- it is not skipped."""
    out = []
    p = Path(path)
    if not p.is_file():
        return out
    for line in p.read_text(encoding='utf-8').splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def one(rows, state):
    hits = [r for r in rows if r.get('state') == state]
    return hits[0] if len(hits) == 1 else None


def size_of(path):
    return path.stat().st_size if path.is_file() else None


class Resp:
    """A body shaped like the OpenAI-compatible gateway ask() speaks to."""

    def __init__(self):
        self._b = json.dumps(PAYLOAD).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._b


def make_fake(boom=None):
    """Stand-in for the HTTP transport. It records what the ledger file held AT CALL TIME,
    read through coder.MODEL_CALLS so the writer and this reader agree on the destination."""
    def fake(req, timeout=None):
        seen.append({'rows': rows_of(coder.MODEL_CALLS), 'timeout': timeout})
        if boom is not None:
            raise boom
        return Resp()
    return fake


def call_coder(prompt, model, max_tokens, boom=None):
    """One ask() with the transport AND the telemetry writer replaced, both restored.

    _tel is stubbed because it writes to the repo (run_telemetry.record appends to
    workspace/run-telemetry.jsonl). A fixture that touches a real ledger is not a fixture.
    """
    real, real_tel = coder.urllib.request.urlopen, coder._tel
    coder.urllib.request.urlopen = make_fake(boom)
    coder._tel = lambda *a, **k: tel.append((a, k))
    try:
        return coder.ask(prompt, model, max_tokens=max_tokens, timeout=TIMEOUT_SECS), None
    except Exception as e:                                        # noqa: BLE001
        return None, e
    finally:
        coder.urllib.request.urlopen = real
        coder._tel = real_tel


def test_a_started_row_is_written_before_the_request():
    """The load-bearing assertion: STARTED must be on disk at the moment the request opens,
    so a call that never returns has left evidence behind. A writer that records only after
    the reply -- the shape that leaves a hang invisible -- fails here."""
    del seen[:]
    del tel[:]
    res, err = call_coder('PROMPT', MODEL, MAX_TOKENS)
    check('ask() still returns (content, token count, seconds) unchanged',
          res is not None and len(res) == 3 and res[0] == 'ANSWER' and res[1] == 7
          and isinstance(res[2], float), repr((res, err)))
    check('ask.last still carries done_reason for the attempt loop (t238 contract)',
          coder.ask.last.get('done_reason') == 'stop', repr(coder.ask.last))
    check('ask() still records telemetry for the call (this task must not delete it)',
          len(tel) == 1 and tel[0][0] and tel[0][0][0] == 'coder.py/gateway', repr(tel))
    check('exactly one HTTP request is issued, carrying the timeout passed in',
          len(seen) == 1 and seen[0].get('timeout') == TIMEOUT_SECS, repr(seen))
    at_call = seen[0]['rows'] if seen and 'rows' in seen[0] else []
    started = one(at_call, 'STARTED')
    check('a STARTED row was on disk AT THE MOMENT the request went out',
          started is not None, repr(at_call))
    check('and no COMPLETED row existed yet: STARTED is not written late',
          not any(r.get('state') == 'COMPLETED' for r in at_call), repr(at_call))
    if started is not None:
        for k in ('ts', 'call_id', 'model', 'max_tokens', 'timeout', 'state'):
            check('STARTED row carries a real ' + k, started.get(k) is not None,
                  repr(sorted(started)))
        check('STARTED row says STARTED', started.get('state') == 'STARTED',
              repr(started.get('state')))
        check('STARTED names the model this call ran',
              started.get('model') == MODEL, repr(started.get('model')))
        check("STARTED records THIS call's output budget (4321, not the 16000 default)",
              started.get('max_tokens') == MAX_TOKENS, repr(started.get('max_tokens')))
        check('STARTED carries the timeout a watcher needs to call it hung',
              started.get('timeout') == TIMEOUT_SECS, repr(started.get('timeout')))
        check("STARTED's ts is a whole ISO-8601 UTC stamp, not a fragment",
              isinstance(started.get('ts'), str) and len(started.get('ts')) == 20
              and started.get('ts').endswith('Z'), repr(started.get('ts')))
    after = rows_of(DEST)
    done = one(after, 'COMPLETED')
    check('a COMPLETED row exists once the reply is parsed',
          done is not None, repr([r.get('state') for r in after]))
    if started is not None and done is not None:
        check('COMPLETED joins THIS call by its call_id',
              done.get('call_id') == started.get('call_id'),
              repr((done.get('call_id'), started.get('call_id'))))
        check('COMPLETED records done_reason FROM THE REPLY, not a constant',
              done.get('done_reason') == 'stop', repr(done.get('done_reason')))
        check('COMPLETED records elapsed seconds as a number',
              isinstance(done.get('elapsed_s'), (int, float)), repr(done.get('elapsed_s')))
        check('COMPLETED records the tokens this call evaluated (7, from usage)',
              done.get('eval_count') == 7, repr(done.get('eval_count')))
        check('COMPLETED records the prompt tokens this call sent (11, from usage)',
              done.get('prompt_eval_count') == 11, repr(done.get('prompt_eval_count')))
        check('one call writes exactly one STARTED then one COMPLETED',
              [r.get('state') for r in after] == ['STARTED', 'COMPLETED'],
              repr([r.get('state') for r in after]))


def test_a_dead_call_leaves_started_and_no_completed():
    """The hang signature, and the anti-swallow guard. A transport that dies mid-call must
    propagate as it does today AND leave its STARTED un-completed. An implementation that
    catches the exception and records COMPLETED anyway -- the tempting shortcut -- fails: a
    hang and a clean reply would then look the same on disk."""
    before = len(rows_of(DEST2))
    res, err = call_coder('PROMPT', MODEL, MAX_TOKENS_B,
                          boom=OSError('simulated socket death mid-call'))
    check('ask() lets a dead transport propagate (it does not swallow it)',
          res is None and isinstance(err, OSError), repr((res, err)))
    new = rows_of(DEST2)[before:]
    check('the dead call left its STARTED row recorded',
          any(r.get('state') == 'STARTED' for r in new),
          repr([r.get('state') for r in new]))
    check('and NO COMPLETED row: STARTED-without-COMPLETED is the hang signature',
          not any(r.get('state') == 'COMPLETED' for r in new),
          repr([r.get('state') for r in new]))


def test_two_calls_get_their_own_ids_and_their_own_values():
    del seen[:]
    del tel[:]
    call_coder('PROMPT A', MODEL, MAX_TOKENS)
    call_coder('PROMPT B', MODEL, MAX_TOKENS_B)
    rows = rows_of(DEST)[2:]
    check('two calls write four rows, paired in order',
          [r.get('state') for r in rows] == ['STARTED', 'COMPLETED', 'STARTED', 'COMPLETED'],
          repr([r.get('state') for r in rows]))
    sids = [r.get('call_id') for r in rows if r.get('state') == 'STARTED']
    check('the two calls get DISTINCT call_ids, so a pair can be joined',
          len(sids) == 2 and len(set(sids)) == 2, repr(sids))
    check('every COMPLETED joins a STARTED 1:1',
          all(r.get('call_id') in sids for r in rows if r.get('state') == 'COMPLETED'),
          repr([r.get('call_id') for r in rows]))
    check("each row records ITS OWN call's max_tokens, not one repeated value",
          sorted(r.get('max_tokens') for r in rows if r.get('state') == 'STARTED')
          == [MAX_TOKENS, MAX_TOKENS_B], repr([r.get('max_tokens') for r in rows]))


if __name__ == '__main__':
    # Import coder.py BY PATH. `import coder` fails from scripts/tests/, which sits on no
    # sys.path entry -- that is a bug in this suite, not the change under test. CODER_ENDPOINT
    # is set FIRST and deliberately omits "11434", which sends ask() down the gateway branch
    # whose transport is the one call_coder replaces, and keeps the module's own endpoint
    # probe from opening a single socket (it honours an explicit endpoint before probing).
    # `console` is stubbed so system_prompt() takes its documented fallback instead of
    # importing the 1000-line console module, whose import-time behaviour is not under test.
    os.environ['CODER_ENDPOINT'] = 'http://127.0.0.1:9/v1/chat/completions'
    _console = types.ModuleType('console')

    def _startup():
        raise RuntimeError('stubbed out by t1241: console is not under test')

    _console.startup = _startup
    sys.modules['console'] = _console
    td = tempfile.mkdtemp(prefix='t1241_')
    DEST = Path(td) / 'model-calls.jsonl'
    DEST2 = Path(td) / 'dead-calls.jsonl'
    live_size, tele_size = size_of(LIVE), size_of(TELE)
    try:
        _spec = importlib.util.spec_from_file_location('coder_under_test', SRC)
        coder = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(coder)
    except Exception as e:                                        # noqa: BLE001
        print('FAIL  coder.py could not be loaded for testing: '
              f'{type(e).__name__}: {e}')
        sys.exit(1)
    check('coder.py exposes the per-call writer this task adds',
          hasattr(coder, 'log_model_call'),
          'the module has no log_model_call: ask() records no row of its own')
    check('its destination is a module constant a caller can redirect',
          hasattr(coder, 'MODEL_CALLS'),
          'the module has no MODEL_CALLS: ask() has nowhere to record a call')
    coder.MODEL_CALLS = DEST
    for fn in (test_a_started_row_is_written_before_the_request,
               test_two_calls_get_their_own_ids_and_their_own_values):
        try:
            fn()
        except Exception as e:                                    # noqa: BLE001
            check(fn.__name__ + ' ran without raising', False, f'{type(e).__name__}: {e}')
    coder.MODEL_CALLS = DEST2
    try:
        test_a_dead_call_leaves_started_and_no_completed()
    except Exception as e:                                        # noqa: BLE001
        check('test_a_dead_call_leaves_started_and_no_completed ran without raising',
              False, f'{type(e).__name__}: {e}')
    check("the repo's model-calls.jsonl is byte-for-byte untouched",
          size_of(LIVE) == live_size, repr((live_size, size_of(LIVE))))
    check("the repo's run-telemetry.jsonl is byte-for-byte untouched",
          size_of(TELE) == tele_size, repr((tele_size, size_of(TELE))))
    print('\n' + ('FAILURES: ' + ', '.join(fails) if fails else 'all assertions passed'))
    sys.exit(1 if fails else 0)
