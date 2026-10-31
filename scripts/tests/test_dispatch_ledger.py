#!/usr/bin/env python3
'''t1045: every infer.chat() dispatch appends one machine-readable row to the dispatch ledger.

WHY (workspace/rca/61fc375f67ab.md). On 2026-08-31 a batch ran nine long jobs on a --model default
of local, a gateway role name. The gateway was down, resolution fell through to the Ollama station,
Ollama answered that model local is not found, and the only account of it anywhere was a
model-quality row saying the model produced nothing -- for tasks it never saw. Nothing recorded
which name was requested, which address it resolved to, and which model the endpoint acknowledged,
so the silence was structural: infer.py has no writer to call (no DISPATCH_LEDGER, no
record_dispatch, no Unacknowledged), so every check in this file is red on main for that reason.

THE FIXTURE SENDS NO TRAFFIC. Every engine probe and every send is replaced in-process by stub();
it answers from a list and raises on any address it was not written to answer. It reads no live
registry (infer.registry is replaced), no settings file, no model, and writes only inside the temp
directory it creates. The dispatch body it sends is the shape settings.request builds; that shape
is asserted below, so the contract is named, not summarised.

The checks that pin the response shape and the exception types pass on main on purpose: they are
the contract the writer must not disturb, so a red run is red about the ledger, not the transport.
'''
import io
import json
import os
import re
import sys
import tempfile
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'projects' / 'local-inference'))
import infer                                                # noqa: E402

FAILS = []
REQUIRED = ['ts', 'caller', 'requested_role', 'requested_model', 'resolved_endpoint',
            'model_acknowledged', 'http_status', 'latency_ms', 'batch_id', 'task_id', 'ok']
# settings.request (projects/local-inference/settings.py) returns exactly these top-level keys.
# The oracle builds that body itself so it never reads the live settings file.
CONTRACT_KEYS = ['keep_alive', 'messages', 'model', 'options', 'stream', 'think']
URL = 'http://127.0.0.1:11434'
CHAT = 'api' + '/chat'
CHAT_URL = URL + '/' + CHAT
REG = {'devices': {'box': {'memory_gb': 121, 'reserve_gb': 8}},
       'servers': {'ol': {'engine': 'ollama', 'url': URL, 'device': 'box'}}}
ENV_KEYS = ('HIVEMIND_BATCH', 'HIVEMIND_TASK')
NEW_NAMES = ('DISPATCH_LEDGER', 'record_dispatch', 'Unacknowledged')
_MISSING = object()


def check(name, got, want):
    ok = got == want
    print('  [' + ('ok' if ok else 'FAIL') + ']  ' + name
          + ('' if ok else ': ' + repr(got) + ' (want ' + repr(want) + ')'))
    if not ok:
        FAILS.append(name)


def body_for(model, role=None):
    b = {'model': model, 'stream': False, 'think': False, 'keep_alive': '5m',
         'messages': [{'role': 'user', 'content': 'what model are you'}],
         'options': {'temperature': 0.0, 'num_predict': 64}}
    if role is not None:
        b['role'] = role
    return b


def rows_of(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding='utf-8').splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def stub(offered, reply=None, raises=None):
    '''Answer every engine probe from `offered`; answer every send from `reply` / `raises`.

    A stub that answers ANY address is a fixture measuring the machinery, not the code, so a
    request to any address other than CHAT_URL raises AssertionError.
    '''
    def models(self):
        return dict((m, 17.7) for m in offered)

    def loaded(self):
        return dict((m, 17.6) for m in offered)

    def health(self):
        return True

    def post(url, body, timeout):
        if str(url) != CHAT_URL:
            raise AssertionError('unexpected request to ' + str(url))
        if raises is not None:
            raise raises
        return dict(reply)

    for cls in (infer.Ollama, infer.OpenAI):
        cls.models, cls.loaded, cls.health = models, loaded, health
    infer._post = post


def dispatch(body, ledger, env):
    '''One infer.chat() against `ledger`. Returns (response, exception, rows).'''
    infer.DISPATCH_LEDGER = Path(ledger)
    exc = None
    resp = None
    try:
        resp = infer.chat(body, timeout=60, env=env)
    except Exception as e:                                    # noqa: BLE001 -- recorded below
        exc = e
    return resp, exc, rows_of(ledger)


def one_row(rows, case):
    check(case + ' appends exactly one row', len(rows), 1)
    return rows[0] if rows else {}


def case_success(led):
    stub(['q4:latest'], reply={'model': 'q4:latest', 'done_reason': 'stop', 'eval_count': 3,
                               'message': {'role': 'assistant', 'content': 'ok'}})
    os.environ['HIVEMIND_BATCH'] = 'b-77'
    os.environ['HIVEMIND_TASK'] = 't1045'
    body = body_for('q4:latest', role='coder')
    check('the dispatch body carries the settings.request contract keys',
          sorted(set(CONTRACT_KEYS) & set(body)), sorted(CONTRACT_KEYS))
    resp, exc, rows = dispatch(body, led, {})
    check('a clean dispatch raises nothing', repr(exc), 'None')
    check('the caller still gets the endpoint reply back',
          (resp or {}).get('message', {}).get('content'), 'ok')
    check('the caller is still told which server answered', (resp or {}).get('_server'), 'ol')
    r = one_row(rows, 'a clean dispatch')
    check('every field a reader needs is on the row',
          sorted(k for k in REQUIRED if k not in r), [])
    check('the name that was requested', r.get('requested_model'), 'q4:latest')
    check('the role the caller asked for, verbatim', r.get('requested_role'), 'coder')
    check('the model the endpoint acknowledged with its own name',
          r.get('model_acknowledged'), 'q4:latest')
    check('the address the request resolved to', r.get('resolved_endpoint'), URL)
    check('a clean dispatch records status 200', r.get('http_status'), 200)
    check('a clean dispatch is a terminal ok row', r.get('ok'), True)
    check('batch_id is the batch the job runs in', r.get('batch_id'), 'b-77')
    check('task_id is the task being worked', r.get('task_id'), 't1045')
    check('latency_ms is a non-negative number',
          isinstance(r.get('latency_ms'), (int, float))
          and not isinstance(r.get('latency_ms'), bool) and r.get('latency_ms') >= 0, True)
    check('ts is a date-time a person can read',
          bool(re.match(r'^\d{4}-\d{2}-\d{2}[T ]', str(r.get('ts')))), True)
    check('caller names the script:line that dispatched',
          bool(re.search(r'test_dispatch_ledger\.py:\d+$', str(r.get('caller')))), True)
    second, exc2, rows2 = dispatch(body_for('q4:latest'), led, {})
    check('a second dispatch appends, it does not overwrite', len(rows2), 2)
    check('and the first row is intact',
          [x.get('requested_model') for x in rows2[:2]], ['q4:latest', 'q4:latest'])
    check('a body with no role records no role, never the model name',
          rows2[1].get('requested_role') in (None, ''), True)
    check('a second clean dispatch raises nothing', repr(exc2), 'None')


def case_endpoint_refuses(led):
    err = urllib.error.HTTPError(CHAT_URL, 404, 'Not Found', {}, io.BytesIO(b''))
    stub(['local'], raises=err)
    for k in ENV_KEYS:
        os.environ.pop(k, None)
    resp, exc, rows = dispatch(body_for('local'), led, {})
    check('the endpoint refusal reaches the caller, it is not swallowed',
          type(exc).__name__, 'HTTPError')
    check('and the message is the endpoint message, not a code', str(exc), 'HTTP Error 404: Not Found')
    r = one_row(rows, 'an endpoint that answers 404')
    check('the row names the model that was requested', r.get('requested_model'), 'local')
    check('the row records that the endpoint acknowledged nothing', r.get('model_acknowledged'), None)
    check('the row carries the endpoint status code', r.get('http_status'), 404)
    check('a refusal is NOT a terminal ok row', r.get('ok'), False)
    check('a body with no role records no role (never the model name)',
          r.get('requested_role') in (None, ''), True)
    check('with no batch in the environment batch_id is present and unfabricated',
          ('batch_id' in r, r.get('batch_id') in (None, '')), (True, True))
    check('with no task in the environment task_id is present and unfabricated',
          ('task_id' in r, r.get('task_id') in (None, '')), (True, True))


def case_endpoint_down(led):
    stub(['local'], raises=urllib.error.URLError('connection refused'))
    resp, exc, rows = dispatch(body_for('local'), led, {})
    check('the connection failure reaches the caller', type(exc).__name__, 'URLError')
    r = one_row(rows, 'an endpoint that is down')
    check('the row names the model that was requested', r.get('requested_model'), 'local')
    check('a connection failure records no status: there is no answer to record',
          r.get('http_status'), None)
    check('a connection failure records no acknowledgement', r.get('model_acknowledged'), None)
    check('a down endpoint is NOT a terminal ok row', r.get('ok'), False)
    check('the address it was sent to is still recorded', r.get('resolved_endpoint'), URL)


def case_no_server_offers_it(led):
    stub([], raises=AssertionError('no request is sent when nothing offers the model'))
    resp, exc, rows = dispatch(body_for('local'), led, {})
    check('a name no server offers still raises NoServer', type(exc).__name__, 'NoServer')
    r = one_row(rows, 'a name no server offers')
    check('the row names the role name that was sent as a model', r.get('requested_model'), 'local')
    check('it records that nothing resolved', r.get('resolved_endpoint'), None)
    check('it records no acknowledgement', r.get('model_acknowledged'), None)
    check('it records no status: nothing answered', r.get('http_status'), None)
    check('a dispatch that never reached an endpoint is NOT a terminal ok row', r.get('ok'), False)


def case_answers_another_model(led):
    stub(['local'], reply={'model': 'q4:latest', 'done_reason': 'stop',
                           'message': {'role': 'assistant', 'content': 'answered'}})
    resp, exc, rows = dispatch(body_for('local'), led, {})
    check('chat refuses a reply that answers for a model it was not asked for',
          type(exc).__name__, 'Unacknowledged')
    check('and does not hand the caller a reply for the wrong model', repr(resp), 'None')
    check('the refusal names both names, so the record says what mismatched',
          ('local' in str(exc), 'q4:latest' in str(exc)), (True, True))
    r = one_row(rows, 'an endpoint that answers for a different model')
    check('the row keeps the requested name', r.get('requested_model'), 'local')
    check('and the name the endpoint answered with, not the requested one',
          r.get('model_acknowledged'), 'q4:latest')
    check('an unacknowledged dispatch is NOT a terminal ok row', r.get('ok'), False)
    check('an unacknowledged dispatch carries a status, not a silence', r.get('http_status'), 200)


def case_writer_shape(led):
    infer.DISPATCH_LEDGER = Path(led)
    row = dict((k, 'x') for k in REQUIRED)
    row['ok'] = True
    row['http_status'] = 200
    row['latency_ms'] = 1
    infer.record_dispatch(row, Path(led))
    written = rows_of(led)
    check('record_dispatch writes the row whole', len(written), 1)
    check('and drops no field it was given',
          sorted(written[0].keys()) if written else None, sorted(REQUIRED))
    short = dict(row)
    short.pop('caller')
    raised = None
    try:
        infer.record_dispatch(short, Path(led))
    except Exception as e:                                    # noqa: BLE001 -- recorded below
        raised = e
    check('a row missing a required field is refused, not written hollow',
          type(raised).__name__ != 'NoneType', True)
    check('and nothing partial is appended', len(rows_of(led)), 1)


CASES = [case_success, case_endpoint_refuses, case_endpoint_down, case_no_server_offers_it,
         case_answers_another_model, case_writer_shape]


def run_case(fn, ledger):
    try:
        fn(ledger)
    except Exception as e:                                    # noqa: BLE001 -- reported as a failure
        check(fn.__name__ + ' runs to completion', type(e).__name__ + ': ' + str(e), 'no exception')


def main():
    saved = dict((c, (c.models, c.loaded, c.health)) for c in (infer.Ollama, infer.OpenAI))
    saved_post, saved_registry = infer._post, infer.registry
    saved_ledger = getattr(infer, 'DISPATCH_LEDGER', _MISSING)
    saved_env = dict((k, os.environ.get(k)) for k in ENV_KEYS)
    try:
        infer.registry = lambda path=None: REG
        for name in NEW_NAMES:
            check('infer exposes ' + name
                  + ' (absent on main: that is what this suite exists for)',
                  hasattr(infer, name), True)
        with tempfile.TemporaryDirectory() as td:
            for fn in CASES:
                run_case(fn, Path(td) / (fn.__name__ + '.jsonl'))
    finally:
        for c, v in saved.items():
            c.models, c.loaded, c.health = v
        infer._post, infer.registry = saved_post, saved_registry
        if saved_ledger is _MISSING:
            if hasattr(infer, 'DISPATCH_LEDGER'):
                del infer.DISPATCH_LEDGER
        else:
            infer.DISPATCH_LEDGER = saved_ledger
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print('  ' + ('FAILED: ' + ', '.join(FAILS) if FAILS else 'test_dispatch_ledger: ok'))
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
