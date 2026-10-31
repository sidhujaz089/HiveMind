#!/usr/bin/env python3
'''t1178: the lease must hand out the slots the server that runs the model has.

WHY. 2026-10-14 (workspace/tasks/t1178--harness-single-slot-llama-server-over-subscribed.md):
six coder.py dispatches ran at once against a llama-server started with -np 1. The requests
queued inside the server, one connection was reset mid-generation (ConnectionResetError) and
the reply came back cut off: unexpected EOF in multi-line statement, (597, 0). Those dispatches
were granted their slots by scripts/modellease.py, which takes the number from somewhere other
than the engine that answers the model:

  - for a non-Ollama engine, slots() returns the slots key written on that server's entry in
    projects/local-inference/models/SERVERS.json and never asks the engine -- _engine_slots
    reads s.entry.get("slots") and returns it;
  - slot_paths() passes no model to slots(), so _engine_slots(None) returns None and the count
    comes from the MAIN Ollama unit for a model the registry serves from a different box:
    SERVERS.json puts gemma4:e4b on ollama-thinkpad, not on the main one.

The module already knows this shape of defect: serial_archs() reads the journal because the
unit said -np 4 while the load line said -np 1, n_slots = 1. The declared slots key is a
CEILING -- SERVERS.json documents it as a hand-set vLLM number, and its own history shows it
moved by hand 4, 8, 6, 1 -- so the fix is a min(), not a replacement, and a server with no
/slots endpoint keeps the number it declares.

No live server, no model call, no repo writes, no fixed /tmp: the lease lives in a temp dir
(modellease._ledger skips any lease outside the repository) and every request the code makes is
answered by the stub below, which REFUSES any URL it was not written to answer. The stub never
serves a generation endpoint, so a build that tried to count slots by asking the model to
answer something would fail rather than pass.
'''
import contextlib
import json
import sys
import tempfile
import types
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import modellease  # noqa: E402

MODEL = 'gemma4:e4b'
ENGINE = 'openai'
SLOTS_ENDPOINT = '/slots'
CHAT_ENDPOINT = '/api' + '/chat'


class _Server:
    '''The shape scripts/modellease.py already reads off infer.servers(): an object
    carrying an entry dict with engine, models, slots and url.'''

    def __init__(self, entry):
        self.entry = entry


class _Response:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _install(port, declared, slots_reply=None, raw_reply=None, unreachable=False):
    '''One OpenAI-compatible engine serving MODEL on 127.0.0.1, its declared ceiling,
    a request log, and a stub answering ONLY its /slots endpoint.'''
    addr = 'http://127.0.0.1:' + str(port)
    entry = {'engine': ENGINE, 'models': {MODEL: {}}, 'slots': declared, 'url': addr}
    fake_infer = types.ModuleType('infer')
    fake_infer.servers = lambda reg=None: {'thinkpad-coder': _Server(entry)}
    fake_routing = types.ModuleType('task_routing')
    fake_routing.load = lambda: {}
    fake_routing.model_lanes = lambda data, model: None
    seen = []

    def answer(req, *args, **kw):
        url = getattr(req, 'full_url', str(req))
        timeout = kw.get('timeout', args[0] if args else None)
        seen.append((url, timeout))
        if not url.endswith(SLOTS_ENDPOINT):
            raise AssertionError('oracle: unexpected request to ' + url + ' -- this suite answers '
                                 + SLOTS_ENDPOINT + ' only')
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise AssertionError('oracle: the slot probe must carry a positive timeout= argument; '
                                 'an unbounded probe is the 600 s hang written in the task note')
        if unreachable:
            raise OSError('connection refused (simulated: the engine is not answering '
                          + SLOTS_ENDPOINT + ')')
        if raw_reply is not None:
            return _Response(json.dumps(raw_reply).encode('utf-8'))
        return _Response(json.dumps([{'id': i, 'state': 'idle'} for i in range(slots_reply)])
                         .encode('utf-8'))

    return entry, seen, fake_infer, fake_routing, answer


@contextlib.contextmanager
def engine_case(port, declared, slots_reply=None, raw_reply=None, unreachable=False):
    '''A lease in a temp dir, the engine as a stub, and _unit_slots patched to FAIL.

    The last line is the load-bearing one: today slot_paths() reaches the MAIN Ollama unit, and a
    build that keeps that fall-through would otherwise pass by reading a number that has nothing
    to do with the server serving the model.'''
    _entry, seen, fake_infer, fake_routing, answer = _install(
        port, declared, slots_reply, raw_reply, unreachable)
    saved = (sys.modules.get('infer'), sys.modules.get('task_routing'),
             urllib.request.urlopen, modellease._unit_slots)
    sys.modules['infer'] = fake_infer
    sys.modules['task_routing'] = fake_routing
    urllib.request.urlopen = answer

    def _no_unit(path):
        raise AssertionError('the engine answered, so the number must come from the engine; '
                             'reading it from the main Ollama unit is the defect this suite exists for')

    modellease._unit_slots = _no_unit
    tmp = tempfile.TemporaryDirectory(prefix='t1178-')
    lease = Path(tmp.name) / 'leases' / 'gemma4_e4b.json'
    lease.parent.mkdir(parents=True, exist_ok=True)
    try:
        yield lease, seen
    finally:
        modellease._unit_slots = saved[3]
        urllib.request.urlopen = saved[2]
        for name, old in (('infer', saved[0]), ('task_routing', saved[1])):
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
        tmp.cleanup()


def test_declared_ceiling_cannot_outrun_the_physical_slots():
    with engine_case(8091, declared=4, slots_reply=1) as (lease, seen):
        n = modellease.slots(lease, model=MODEL)
    assert type(n) is int, 'slots() must hand back a whole number of slots, got %r' % (n,)
    assert n == 1, ('a llama-server started with -np 1 has ONE slot: slots() returned %r while the '
                    'engine serving %s reported 1. The slots key on the server entry is a ceiling, '
                    'not a measurement -- 4 slots against a 1-slot server is what starved six '
                    'dispatches into a connection reset and a truncated reply.' % (n, MODEL))
    probed = [u for u, _t in seen if u.endswith(SLOTS_ENDPOINT)]
    assert probed, ('no request reached the engine that serves the model, so the number is a '
                    'declaration and not a measurement')
    assert ':8091' in probed[0], ('capacity must be read from the server that serves the model, not '
                                   'from a hardcoded address: the probe went to ' + probed[0])
    assert not [u for u, _t in seen if CHAT_ENDPOINT in u], ('slots are counted at the slot endpoint, '
                                                             'never by occupying a slot with a '
                                                             'generation request')


def test_a_server_that_really_multiplexes_is_not_under_served():
    with engine_case(8092, declared=4, slots_reply=4) as (lease, seen):
        n = modellease.slots(lease, model=MODEL)
    assert n == 4, ('a server reporting 4 slots must keep 4: got %r. A lease of 1 on a server that '
                    'multiplexes serialises every job behind the first and starves the box it is '
                    'meant to feed.' % (n,))


def test_the_declared_ceiling_still_binds():
    with engine_case(8093, declared=2, slots_reply=5) as (lease, seen):
        n = modellease.slots(lease, model=MODEL)
    assert n == 2, ('a probe must not raise the Models-tab number above its declared ceiling: '
                    'declared 2, engine reported 5, slots() returned %r.' % (n,))


def test_an_unreachable_probe_fails_open_to_the_declared_number():
    with engine_case(8094, declared=3, unreachable=True) as (lease, seen):
        n = modellease.slots(lease, model=MODEL)
    assert n == 3, ('an engine that does not answer its slot endpoint must leave the declared number '
                    'standing: declared 3, slots() returned %r. The interlock must not become the '
                    'outage.' % (n,))
    assert [u for u, _t in seen if u.endswith(SLOTS_ENDPOINT)], ('the probe was attempted; a '
                                                                  'fail-open that never probed is a '
                                                                  'silent default, not a fallback')


def test_a_probe_reporting_zero_never_leaves_the_model_unleasable():
    with engine_case(8095, declared=4, slots_reply=0) as (lease, seen):
        n = modellease.slots(lease, model=MODEL)
    assert n == 1, ('a server reporting 0 slots must floor to 1: slots() returned %r. Zero slots '
                    'means every dispatch for this model is refused forever, which is a worse '
                    'failure than the over-subscription being fixed.' % (n,))


def test_a_server_with_no_slots_endpoint_keeps_its_declared_ceiling():
    with engine_case(8096, declared=6, raw_reply={'error': 'Not Found'}) as (lease, seen):
        n = modellease.slots(lease, model=MODEL)
    assert n == 6, ('an engine that answers an object where a list of slots belongs (vLLM serves no '
                    'slot list) keeps the number it declares: declared 6, slots() returned %r.' % (n,))


def test_the_lease_grants_only_the_slots_the_server_has():
    with engine_case(8097, declared=4, slots_reply=1) as (lease, seen):
        try:
            paths = modellease.slot_paths(lease, model=MODEL)
        except TypeError as e:
            raise AssertionError('slot_paths takes no model, so every lease under workspace/leases/ '
                                'counts the slots of the MAIN Ollama unit, including for models the '
                                'registry serves from other engines: ' + str(e))
    assert len(paths) == 1, ('a -np 1 server must yield ONE slot file for its lease: got %d -- '
                             '%r' % (len(paths), paths))
    assert all(p.parent == lease.parent for p in paths), ('slot files must sit beside the lease they '
                                                          'belong to: %r' % (paths,))


def _run():
    names = sorted(k for k in list(globals()) if k.startswith('test_') and callable(globals()[k]))
    failed = []
    for name in names:
        try:
            globals()[name]()
            print('  ok    ' + name)
        except Exception as e:
            failed.append(name)
            print('  FAIL  ' + name + ': ' + type(e).__name__ + ': ' + str(e))
    print('%d/%d passed' % (len(names) - len(failed), len(names)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run())
