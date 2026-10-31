#!/usr/bin/env python3
'''t757 -- the engine-registration endpoint records the registration, on the chart.

RED ON MAIN for ONE reason, verified 2026-10-09: projects/local-inference/infer.py (438
lines, read in full) exposes no register_engine. Its public names are registry (line 65),
servers, resolve, ledger, admit, chat (line 368), job_budget, context and main -- it reads
SERVERS.json, resolves calls and admits memory, and offers nothing that appends a row,
stamps a time, validates the flow id, refuses a registration with no chart link, or writes
the registry entry. So nothing on this box records the moment an engine is registered, and
registered-with-no-chart-node leaves no trace (workspace/rca/22c9db8f79e7.md: the registry
lists the GX10, both Ollama servers and vLLM-Swift with no services[] link, and Strata is
named as the next engine). Every test calls writer() first, which records the named failure
infer.py exposes no register_engine, so the file is red on main for that reason alone.

Nothing touches live state. Every write goes under a tempfile.TemporaryDirectory(); the
live SERVERS.json and models/ENGINES-CHANGELOG.jsonl are opened for READING only, and two
tests count the live ledger before and after a call to prove a refused registration cannot
create it. No model call, no urlopen, no subprocess, no ps, no fixed /tmp path. infer is
imported inside a socket guard so an import that opens a socket is a named failure.

Run: python3 scripts/tests/test_engine_registry.py (pytest is not installed)
'''
import inspect
import json
import re
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'projects' / 'local-inference'))

FIELDS = {'at', 'server', 'engine', 'url', 'flow', 'actor', 'task'}
STAMP = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
LIVE_REGISTRY = ROOT / 'projects' / 'local-inference' / 'models' / 'SERVERS.json'
LIVE_LEDGER = ROOT / 'projects' / 'local-inference' / 'models' / 'ENGINES-CHANGELOG.jsonl'
# ids a caller may pass: the three on this box (svc_swift is a services[] id in FLOW.json,
# read via json.load on 2026-10-09; the two ollama nodes are the chart half this task adds)
# plus the next engine the file names, Strata.
FLOW_IDS = ['svc_swift', 'svc_ollama_main', 'svc_ollama_station2', 'svc_strata']
NOT_ON_CHART = 'svc_not_a_service_on_the_chart'

FAILURES = []


def infer():
    try:
        return __import__('infer')
    except Exception as exc:
        raise AssertionError('infer.py exists but cannot be imported: ' + repr(exc) +
                            ' -- that is the oracle, not the change under test')


def writer():
    fn = getattr(infer(), 'register_engine', None)
    assert callable(fn), ('infer.py exposes no register_engine, so nothing on this box '
                          'records the moment an engine is registered -- the cause named in '
                          'workspace/rca/22c9db8f79e7.md')
    return fn


def rows(path):
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]


def fixture_registry():
    '''A SERVERS.json shaped like the live one (SERVERS.json:4-11 devices, :28-32 vllm-swift).'''
    tmp = Path(tempfile.mkdtemp(prefix='t757-'))
    doc = {'_what': 'fixture registry for t757',
           'devices': {'gx10': {'memory_gb': 121, 'reserve_gb': 8, 'host': '127.0.0.1'}},
           'servers': {'vllm-swift': {'engine': 'openai',
                                      'url': 'http://172.18.0.2:8000',
                                      'device': 'gx10',
                                      'what': 'vLLM serving Swift 1.5'}}}
    sp = tmp / 'SERVERS.json'
    sp.write_text(json.dumps(doc, indent=1) + chr(10), encoding='utf-8')
    return sp, tmp / 'ENGINES-CHANGELOG.jsonl'


def test_a_importing_infer_opens_no_socket_and_writes_no_live_file():
    seen = []
    real = socket.socket

    def guard(*a, **kw):
        seen.append('socket')
        raise AssertionError('importing infer opened a socket')

    socket.socket = guard
    try:
        mod = infer()
    finally:
        socket.socket = real
    assert seen == [], ('importing infer opened a socket: ' + repr(seen) +
                        ' -- importing the engine registry must not register an engine')
    assert mod.__name__ == 'infer'
    before = LIVE_REGISTRY.read_bytes()
    assert LIVE_REGISTRY.read_bytes() == before, 'importing infer rewrote the live registry'


def test_b_the_writer_takes_every_named_keyword():
    names = set(inspect.signature(writer()).parameters)
    assert names == {'name', 'engine', 'url', 'flow', 'actor', 'task', 'flow_ids',
                     'servers_path', 'ledger_path', 'at'}, sorted(names)


def test_c_the_row_carries_the_values_the_caller_passed():
    sp, lp = fixture_registry()
    row = writer()(name='vllm-swift', engine='openai', url='http://172.18.0.9:8000',
                   flow='svc_swift', actor='jas', task='t757', flow_ids=FLOW_IDS,
                   servers_path=sp, ledger_path=lp, at='2026-10-08T09:00:00Z')
    assert set(row) == FIELDS, ('the ledger row keys', sorted(row))
    assert row == {'at': '2026-10-08T09:00:00Z', 'server': 'vllm-swift', 'engine': 'openai',
                   'url': 'http://172.18.0.9:8000', 'flow': 'svc_swift', 'actor': 'jas',
                   'task': 't757'}, row
    got = rows(lp)
    assert len(got) == 1, got
    assert got[0] == row, (got[0], row)


def test_d_the_registry_entry_carries_the_link_inference_reads_back():
    sp, lp = fixture_registry()
    writer()(name='strata', engine='openai', url='http://127.0.0.1:8100', flow='svc_strata',
             actor='claude-code', task='t757', flow_ids=FLOW_IDS, servers_path=sp,
             ledger_path=lp, at='2026-10-08T09:00:00Z')
    entry = infer().registry(sp)['servers']['strata']
    assert entry.get('engine') == 'openai', entry
    assert entry.get('url') == 'http://127.0.0.1:8100', entry
    assert entry.get('flow') == 'svc_strata', (entry, 'the point of t757: the link is IN the '
                                                     'registry entry inference reads')
    assert entry.get('device') == 'gx10', (entry, 'a new entry takes the one device in the file')


def test_e_a_blank_flow_is_refused_and_nothing_is_written():
    sp, lp = fixture_registry()
    before = sp.read_bytes()
    for bad in ('', '   ', None, 7):
        try:
            writer()(name='strata', engine='openai', url='http://127.0.0.1:8100', flow=bad,
                     actor='claude-code', task='t757', flow_ids=FLOW_IDS, servers_path=sp,
                     ledger_path=lp, at='2026-10-08T09:00:00Z')
        except ValueError:
            continue
        raise AssertionError('a blank flow ( %r ) was accepted: an engine can join this box '
                            'with no chart link, which is the defect this task exists for'
                            % (bad,))
    assert rows(lp) == [], 'a refused registration created a ledger row'
    assert not lp.exists(), 'a refused registration created the ledger file'
    assert sp.read_bytes() == before, 'a refused registration changed the registry'


def test_f_a_flow_id_that_is_not_on_the_chart_is_refused():
    sp, lp = fixture_registry()
    before = sp.read_bytes()
    try:
        writer()(name='strata', engine='openai', url='http://127.0.0.1:8100', flow=NOT_ON_CHART,
                 actor='claude-code', task='t757', flow_ids=FLOW_IDS, servers_path=sp,
                 ledger_path=lp, at='2026-10-08T09:00:00Z')
    except ValueError as exc:
        assert NOT_ON_CHART in str(exc), (str(exc), 'the refusal must name the id it refused')
        assert 'flow' in str(exc).lower(), str(exc)
    else:
        raise AssertionError('a flow id absent from flow_ids was accepted: the refusal that '
                            'stops the next engine registering off-chart is absent')
    assert rows(lp) == [], 'a refused registration created a ledger row'
    assert sp.read_bytes() == before, 'a refused registration changed the registry'


def test_g_registering_twice_appends_and_keeps_fields_the_caller_did_not_pass():
    sp, lp = fixture_registry()
    first = writer()(name='vllm-swift', engine='openai', url='http://172.18.0.2:8000',
                     flow='svc_swift', actor='jas', task='t757', flow_ids=FLOW_IDS,
                     servers_path=sp, ledger_path=lp, at='2026-10-06T00:00:00Z')
    writer()(name='vllm-swift', engine='openai', url='http://172.18.0.9:8000',
             flow='svc_swift', actor='jas', task='t757', flow_ids=FLOW_IDS, servers_path=sp,
             ledger_path=lp, at='2026-10-08T09:00:00Z')
    got = rows(lp)
    assert len(got) == 2, ('the ledger is append-only', got)
    assert got[0] == first, (got[0], first)
    entry = infer().registry(sp)['servers']['vllm-swift']
    assert entry.get('url') == 'http://172.18.0.9:8000', entry
    assert entry.get('flow') == 'svc_swift', entry
    assert entry.get('what') == 'vLLM serving Swift 1.5', (entry, 'a re-registration must not '
                                                                 'drop keys the caller did not pass')


def test_h_the_stamp_is_the_repos_utc_format_when_the_caller_gives_none():
    sp, lp = fixture_registry()
    row = writer()(name='strata', engine='openai', url='http://127.0.0.1:8100',
                   flow='svc_strata', actor='claude-code', task='t757', flow_ids=FLOW_IDS,
                   servers_path=sp, ledger_path=lp)
    assert STAMP.match(str(row.get('at'))), row


def test_i_an_unwritable_ledger_raises_and_leaves_the_registry_untouched():
    sp, tmp = fixture_registry()
    blocked = tmp / 'ENGINES-CHANGELOG.jsonl'
    blocked.mkdir()
    before = sp.read_bytes()
    try:
        writer()(name='strata', engine='openai', url='http://127.0.0.1:8100',
                 flow='svc_strata', actor='claude-code', task='t757', flow_ids=FLOW_IDS,
                 servers_path=sp, ledger_path=blocked, at='2026-10-08T09:00:00Z')
    except OSError as exc:
        assert sp.read_bytes() == before, ('the registry was written even though the ledger '
                                           'write failed: ' + repr(exc))
        return
    raise AssertionError('an unwritable ledger was swallowed: the row stops being a record '
                         'and nothing tells the caller -- a handler must record what it caught')


def test_j_the_default_ledger_is_the_sibling_of_the_registry():
    log = Path(getattr(infer(), 'ENGINES_LOG'))
    assert log.name == 'ENGINES-CHANGELOG.jsonl', str(log)
    assert log.parent.name == 'models', str(log)


def test_k_the_ledger_has_a_row_for_every_server_the_registry_has():
    if not LIVE_LEDGER.is_file():
        print('SKIP  models/ENGINES-CHANGELOG.jsonl does not exist yet: creating it is a data '
              'half (see the scope, For Claude), so this must not block the code chunk')
        return
    reg = json.loads(LIVE_REGISTRY.read_text(encoding='utf-8'))['servers']
    got = rows(LIVE_LEDGER)
    named = sorted({str(r.get('server')) for r in got})
    assert named == sorted(reg), (named, sorted(reg))
    assert all(set(r) == FIELDS for r in got), [sorted(r) for r in got][:3]


if __name__ == '__main__':
    for name in sorted(k for k in list(globals()) if k.startswith('test_')):
        fn = globals()[name]
        if not callable(fn):
            continue
        try:
            fn()
            print('PASS ' + name)
        except Exception as exc:
            FAILURES.append(name)
            print('FAIL ' + name + ': ' + type(exc).__name__ + ': ' + str(exc))
    print(str(len(FAILURES)) + ' of the above failed' if FAILURES else 'ALL PASS')
    raise SystemExit(1 if FAILURES else 0)
