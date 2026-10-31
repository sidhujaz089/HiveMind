#!/usr/bin/env python3
'''t361 clause (b): two concurrent chat requests must not queue behind each other.

THE DECAY TEST: multiplexing has to be a durable property of the client, not an accident of
what happens to be loaded. Delete the concurrency from scripts/modellease.py and this goes RED.

DEFAULT MODE (what `python3 scripts/tests/test_slot_concurrency.py` runs; this is the only
mode the builder and the scope lint run): a stdlib fake of the Ollama chat endpoint, whose slot
pool is N wide, bound to 127.0.0.1 on a free port. Two requests fire at once and BOTH results
are collected. No live model is called, no lease is taken, no process is left running, no file
is written.
  pool = 2   both served in parallel, each inside the per-call budget, and the whole exchange
             fits inside ONE call's budget. A client that fired them one after the other takes
             about twice that and fails.
  pool = 1   the second request can only occupy a slot when the first releases it, so its OWN
             measured latency is about twice the first's and it blows the per-call budget. The
             client must report that: all_within_budget is False. This is the task's "the
             fixture MUST FAIL at -np 1" written as code.
The fake programs the serving behaviour, so default mode proves the CLIENT issues requests
concurrently, sends the right body for each lane, collects both, and detects starvation. It
does not prove the box multiplexes: that is --live.

--live (opt-in; Claude/Jas run it against the real box, never by `python3 <this file>`): two
concurrent chats to one NAMED small model must both finish inside 300s. RED at -np 1, GREEN at
-np 2+. Pinned small on purpose: serial_archs() in scripts/modellease.py records that Ollama
refuses parallel requests for some architectures whatever the unit says, so a big Qwen is RED at
every -np and would be measuring the architecture, not the multiplexing.

THE ONE LIVE TOUCH IN DEFAULT MODE: case 3 executes the chat body contract itself,
settings.request(section, model, messages) -- projects/local-inference/settings.py:758 -- and
that resolves num_ctx through settings.model_ctx() and the digest table, so it reads a JSON file
and runs 'ollama list'. Read-only, no slot consumed, and any failure there is recorded in NOTES
and skips the comparison rather than faking a pass.
'''

import argparse
import inspect
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import slot_concurrency
except ImportError:
    import modellease as slot_concurrency

LIVE_HOST = 'http://127.0.0.1:11434'
LIVE_MODEL = 'qwen3.8:1b'
LIVE_BUDGET_S = 300.0
LIVE_TIMEOUT_S = 240.0
MODEL_A = 'model-alpha'
MODEL_B = 'model-beta'
PING = 'ping'
# A serialising client takes about 2 x CALL_DELAY_S (4.0s) and blows the 3.0s budget; a client
# that fires both at once takes about CALL_DELAY_S (2.0s) and fits. The 1.0s of headroom is the
# cost of building each body through settings.request, which runs 'ollama list' (see docstring).
CALL_DELAY_S = 2.0
PER_CALL_BUDGET_S = 3.0
FAKE_TIMEOUT_S = 15.0
CHAT_PATH = '/' + 'api' + '/chat'
NOTES = []


def _fake_ollama(pool, bodies):
    '''A fake whose slot pool is `pool` wide, serving the chat endpoint only.

    Each request holds one slot for CALL_DELAY_S and then answers with done_reason stop.
    ThreadingHTTPServer gives every connection its own thread, so a 1-wide pool makes the
    SECOND concurrent request wait for the first to finish -- the starvation this task is about.
    A request for any other path is recorded in NOTES, not served: a stub must refuse traffic it
    was not written to answer, not serve it whatever is next in the queue.
    '''
    slots = threading.Semaphore(pool)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != CHAT_PATH:
                NOTES.append('fake server asked for ' + repr(self.path) + '; serving nothing')
                self.send_response(404)
                self.end_headers()
                return
            n = int(self.headers.get('Content-Length') or 0)
            raw = self.rfile.read(n)
            try:
                bodies.append(json.loads(raw.decode('utf-8')))
            except (UnicodeDecodeError, ValueError) as exc:
                bodies.append({'undecodable': type(exc).__name__ + ': ' + str(exc)[:160]})
            with slots:
                time.sleep(CALL_DELAY_S)
            payload = json.dumps({'model': bodies[-1].get('model', ''), 'done': True,
                                  'done_reason': 'stop'}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, fmt, *args):
            return

    return ThreadingHTTPServer(('127.0.0.1', 0), Handler)


def _run_against(server, models, budget=PER_CALL_BUDGET_S):
    '''Fire len(models) requests at the fake in ONE call and hand back the client's result dict.'''
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    t.start()
    try:
        return slot_concurrency.issue_concurrent(
            'http://127.0.0.1:' + str(server.server_address[1]), list(models),
            per_call_budget_s=budget, timeout_s=FAKE_TIMEOUT_S)
    finally:
        server.shutdown()
        server.server_close()


_CONTRACT = None
_CONTRACT_TRIED = False


def _contract():
    '''The module that owns the chat body contract, loaded from the path it really lives on.

    `import settings` from a script under scripts/ resolves to a DIFFERENT module: search_repo
    for 'def request(' finds it in projects/local-inference/settings.py:758 and not in
    scripts/settings.py. Returns None, recording why, when it cannot be loaded, so the default
    run stays red for the missing client and not for this.
    '''
    global _CONTRACT, _CONTRACT_TRIED
    if _CONTRACT_TRIED:
        return _CONTRACT
    _CONTRACT_TRIED = True
    import importlib.util
    path = Path(__file__).resolve().parents[2] / 'projects' / 'local-inference' / 'settings.py'
    try:
        spec = importlib.util.spec_from_file_location('_t361_contract', path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not callable(getattr(mod, 'request', None)):
            NOTES.append(str(path) + ' exposes no callable request(): contract comparison skipped')
            return None
        _CONTRACT = mod
    except Exception as exc:                                          # recorded, not swallowed
        NOTES.append('contract module ' + str(path) + ' not loadable: '
                     + type(exc).__name__ + ': ' + str(exc)[:160])
    return _CONTRACT


def test_collects_both_concurrent_results():
    '''Two requests issued at once on a 2-slot server must BOTH come back, each inside the
    per-call budget, and the whole exchange must fit inside ONE call's budget. A client that
    serialises them takes about twice as long and fails -- which is the defect (2026-09-19:
    an interactive session starved by a 3.5h loop in the one slot).
    '''
    bodies = []
    t0 = time.monotonic()
    res = _run_against(_fake_ollama(2, bodies), [MODEL_A, MODEL_B])
    wall = time.monotonic() - t0
    assert isinstance(res, dict), res
    assert res['n'] == 2, res
    assert len(res['results']) == 2, res
    assert res['all_ok'] is True, res
    assert res['per_call_budget_s'] == PER_CALL_BUDGET_S, res
    # Two DISTINCT model tags: the real code has two lanes, and a fixture that asked the same
    # model twice cannot tell a client that collapsed them (t471/t297/t370).
    # Sorted: the server answers each connection on its own thread, so arrival order is not
    # defined. What must hold is that both DISTINCT tags came back, each exactly once.
    assert sorted(r['model'] for r in res['results']) == sorted([MODEL_A, MODEL_B]), res
    for r in res['results']:
        assert r['ok'] is True, r
        assert r['error'] is None, r
        assert r['done_reason'] == 'stop', r
        assert 0.0 < r['secs'] < PER_CALL_BUDGET_S, r
    assert res['all_within_budget'] is True, res
    # The SLOWER lane, not index 1: thread completion order is not defined.
    assert res['wall_secs'] >= max(r['secs'] for r in res['results']), res
    assert wall < PER_CALL_BUDGET_S, (wall, res)
    # Sorted: the fake records bodies in connection-arrival order, which is not lane order.
    assert sorted(b.get('model') for b in bodies) == sorted([MODEL_A, MODEL_B]), bodies
    for b in bodies:
        assert b['messages'] == [{'role': 'user', 'content': PING}], b
        assert b['stream'] is False, b
    # Only the notes this fixture owns: the fake must have served the chat endpoint only.
    # Environment notes (settings.request finding no infer on sys.path, no window reported) are
    # real information the runner prints at the end of the file, not this fixture's failure.
    assert [n for n in NOTES if n.startswith('fake server asked for')] == [], NOTES


def test_detects_single_slot_starvation():
    '''The task's "the fixture MUST FAIL at -np 1", as code. On a 1-slot server the second
    request can only be served after the first releases the slot, so its OWN latency is about
    twice the first's and it blows the per-call budget. A client that reports that lane as
    healthy is reporting a queued request as a served one.
    '''
    bodies = []
    res = _run_against(_fake_ollama(1, bodies), [MODEL_A, MODEL_B])
    assert res['n'] == 2, res
    assert res['all_ok'] is True, res
    # Lane order is not defined: ThreadingHTTPServer serves each connection on its own thread,
    # so the client's two results may arrive in either order. Compare the SLOWER lane against the
    # budget and against the faster lane, never position 1 against position 0.
    secs = [r['secs'] for r in res['results']]
    assert min(secs) < PER_CALL_BUDGET_S, res
    assert max(secs) > PER_CALL_BUDGET_S, res
    assert max(secs) >= 1.5 * min(secs), res
    assert res['all_within_budget'] is False, res
    assert sorted(b.get('model') for b in bodies) == sorted([MODEL_A, MODEL_B]), bodies


def test_request_bodies_come_from_the_settings_contract():
    '''settings.request(section, model, messages) is THE contract for a chat body
    (docs/skills/local-model-loading; projects/local-inference/settings.py:758). This case
    EXECUTES it and compares, so a hand-built body with invented keys cannot pass: the check-
    satisfied-by-dead-path class is a brief that names the contract in prose and a fixture
    that cannot tell. The signature is read by introspection, so this case does not depend on
    an argument count this file has not read.
    '''
    bodies = []
    res = _run_against(_fake_ollama(2, bodies), [MODEL_A, MODEL_B])
    assert res['all_ok'] is True, res
    assert len(bodies) == 2, bodies
    for b in bodies:
        assert b.get('model') in (MODEL_A, MODEL_B), b
        assert isinstance(b.get('messages'), list) and len(b['messages']) == 1, b
        assert b['messages'][0]['role'] == 'user', b
        assert b['messages'][0]['content'] == PING, b
        assert b.get('stream') is False, b
        assert isinstance(b.get('options'), dict) and b['options'], b
    settings = _contract()
    if settings is None:
        return
    required = [name for name, p in inspect.signature(settings.request).parameters.items()
                if p.default is inspect.Parameter.empty
                and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if len(required) != 3:
        NOTES.append('settings.request takes ' + repr(required)
                     + '; the probe passes a section, a model and messages -- comparison skipped')
        return
    expected = {}
    for tag in (MODEL_A, MODEL_B):
        try:
            expected[tag] = dict(settings.request('chat', tag, [{'role': 'user', 'content': PING}]))
        except Exception as exc:                                      # recorded, not swallowed
            NOTES.append('settings.request(chat, ' + tag + ', messages) raised '
                         + type(exc).__name__ + ': ' + str(exc)[:160])
            return
    for b in bodies:
        want = expected[b['model']]
        # 'stream' is the ONE key the client is required to set on the dict it gets back, so it
        # is exempt from the key-set comparison; every other key must come from the contract.
        invented = set(b) - set(want) - {'stream'}
        assert not invented, 'body keys settings.request does not produce: ' + repr(sorted(invented))
        missing = set(want) - set(b)
        assert not missing, 'body is missing contract keys: ' + repr(sorted(missing))
        assert b['options'] == want['options'], (b['options'], want['options'])


def test_live_two_concurrent_requests(model_tag, budget_s):
    '''--live only, never in the default case list. Two concurrent chats to one NAMED small
    model must both finish inside the budget; RED at -np 1, GREEN at -np 2+. Read-only: one
    POST path, no eviction, no lease, no journal, nothing written.
    '''
    res = slot_concurrency.issue_concurrent(LIVE_HOST, [model_tag, model_tag],
                                            per_call_budget_s=budget_s, timeout_s=LIVE_TIMEOUT_S)
    assert res['n'] == 2, res
    assert res['all_ok'] is True, res
    assert res['all_within_budget'] is True, res


CASES = [('test_collects_both_concurrent_results', test_collects_both_concurrent_results),
         ('test_detects_single_slot_starvation', test_detects_single_slot_starvation),
         ('test_request_bodies_come_from_the_settings_contract',
          test_request_bodies_come_from_the_settings_contract)]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description='t361 slot multiplexing oracle')
    ap.add_argument('--live', action='store_true',
                    help='also run the live two-concurrent-requests case against a real Ollama')
    ap.add_argument('--live-model', default=LIVE_MODEL, help='tag the live case pins (default small)')
    ap.add_argument('--budget', type=float, default=LIVE_BUDGET_S,
                    help='per-call budget in seconds for the live case (default 300)')
    ns = ap.parse_args()
    # The default list is the offline list. This is the whole fix for the 2026-10-07
    # oracle-crash: the live case is not here, so `python3 <this file>` contacts no server.
    cases = list(CASES)
    if ns.live:
        cases.append(('test_live_two_concurrent_requests',
                      lambda: test_live_two_concurrent_requests(ns.live_model, ns.budget)))
    failed = []
    for name, fn in cases:
        try:
            fn()
            print('  ok   ' + name)
        except Exception as exc:                                      # reported, not swallowed
            failed.append(name + ': ' + type(exc).__name__ + ': ' + str(exc)[:300])
    for note in NOTES:
        print('NOTE: ' + note)
    for f in failed:
        print('FAIL ' + f)
    if failed:
        print('RED: ' + str(len(failed)) + ' of ' + str(len(cases)) + ' case(s) failed')
        sys.exit(1)
    print('GREEN: ' + str(len(cases)) + ' case(s) passed')
