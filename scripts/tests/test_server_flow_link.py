#!/usr/bin/env python3
'''t757 -- every registered inference engine carries a FLOW.json service link.

RED ON MAIN for ONE reason, verified 2026-10-09 by reading all 76 lines of
projects/local-inference/models/SERVERS.json: no entry in its servers object has a flow
key -- the word flow appears nowhere in the file -- and the add-engine instruction at
SERVERS.json:3 says a new OpenAI-compatible engine is one entry with engine openai and
no code, naming no chart link. That is how an engine joins this box off-chart, which is
what workspace/rca/22c9db8f79e7.md is about: the status line it quotes lists the GX10,
both Ollama servers and vLLM-Swift, and nothing links any of them to a services[] node.
Four of the six tests below fail on that absence.

The values asserted are the registry's REAL server names (SERVERS.json:13 ollama-main,
:28 vllm-swift, :68 ollama-station2) and three DISTINCT FLOW.json service ids, each
pinned to its own server and asserted pairwise distinct: one repeated placeholder value
passes a loose oracle, so this one cannot. svc_swift is a services[] id in FLOW.json
today (read via json.load on 2026-10-09: the 12 ids are svc_hub, svc_console, svc_chat,
svc_terminal, svc_tools, svc_dsh, svc_apprepo, svc_mcp, svc_tunnel, svc_manager,
svc_swift, svc_key). svc_ollama_main and svc_ollama_station2 are NOT on the chart yet --
FLOW.json has no ollama node at all -- so the two service nodes are added to the chart by
Claude first (see the scope's For Claude), and the chart-join test below is the ratchet
that keeps any FUTURE engine, Strata is named as the next one, on the chart.

Reads only: no writes, no model call, no subprocess, no urlopen, no ps, no fixed /tmp
path. Run: python3 scripts/tests/test_server_flow_link.py (pytest is not installed)
'''
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / 'projects' / 'local-inference' / 'models' / 'SERVERS.json'
FLOW = ROOT / 'projects' / 'local-inference' / 'models' / 'FLOW.json'

# The registry's real names, each pinned to its OWN service id (SERVERS.json:13, :28, :68).
EXPECTED = {'ollama-main': 'svc_ollama_main',
            'vllm-swift': 'svc_swift',
            'ollama-station2': 'svc_ollama_station2'}


def servers():
    return json.loads(REGISTRY.read_text(encoding='utf-8'))['servers']


def chart_ids():
    doc = json.loads(FLOW.read_text(encoding='utf-8'))
    return {s.get('id') for s in doc.get('services', [])}


def test_the_registry_still_names_the_three_engines_on_this_box():
    reg = servers()
    for name in EXPECTED:
        assert name in reg, ('server dropped from the registry: ' + name, sorted(reg))


def test_no_registered_server_is_left_without_a_flow_key():
    missing = sorted(n for n, e in servers().items()
                     if not str(e.get('flow') or '').strip())
    assert missing == [], ('registered servers with no flow link: ' + str(missing) +
                           ' -- an engine can be registered off-chart, which is the defect')


def test_each_engine_is_linked_to_its_own_service_id_not_one_shared_placeholder():
    reg = servers()
    for name, want in EXPECTED.items():
        assert name in reg, (name, sorted(reg))
        assert reg[name].get('flow') == want, (name, reg[name].get('flow'), want)
    links = [reg[n]['flow'] for n in EXPECTED if n in reg]
    assert len(set(links)) == len(links), ('the three flow links are not distinct: ' + str(links))


def test_every_flow_link_is_a_service_id_on_the_real_chart():
    ids = chart_ids()
    assert ids, 'FLOW.json has no services[] entries: the chart cannot be read'
    off = sorted((n, e.get('flow')) for n, e in servers().items()
                 if str(e.get('flow') or '').strip() not in ids)
    assert off == [], ('servers registered off-chart: ' + str(off) +
                       ' -- the link must name a services[] id in FLOW.json')


def test_the_add_engine_instruction_names_the_flow_key():
    doc = json.loads(REGISTRY.read_text(encoding='utf-8')).get('_adding_an_engine', '')
    assert 'flow' in doc.lower(), ('SERVERS.json:3 tells the next person how to add an '
                                   'engine and never names the flow key, so the next engine '
                                   'is added off-chart by following the instructions: ' + repr(doc))
    assert 'FLOW.json' in doc, ('the add-engine instruction does not point at FLOW.json: ' + repr(doc))


def test_adding_the_link_did_not_disturb_the_existing_entry_fields():
    reg = servers()
    want = {'ollama-main': ('ollama', 'http://127.0.0.1:11434'),
            'vllm-swift': ('openai', 'http://172.18.0.2:8000'),
            'ollama-station2': ('ollama', 'http://127.0.0.1:11435')}
    for name, (engine, url) in want.items():
        assert name in reg, (name, sorted(reg))
        assert reg[name].get('engine') == engine, (name, reg[name].get('engine'))
        assert reg[name].get('url') == url, (name, reg[name].get('url'))
    doc = json.loads(REGISTRY.read_text(encoding='utf-8'))
    assert 'gx10' in doc.get('devices', {}), 'the devices block must survive the change'
    assert doc['servers']['vllm-swift']['models']['swift']['footprint_gb'] == 95, \
        'vllm-swift lost its models block'
    assert doc['servers']['ollama-main'].get('default') is True, 'ollama-main lost default: true'


if __name__ == '__main__':
    failed = []
    for name in sorted(k for k in list(globals()) if k.startswith('test_')):
        fn = globals()[name]
        if not callable(fn):
            continue
        try:
            fn()
            print('PASS ' + name)
        except Exception as exc:
            failed.append(name)
            print('FAIL ' + name + ': ' + type(exc).__name__ + ': ' + str(exc))
    print(str(len(failed)) + ' of the above failed' if failed else 'ALL PASS')
    raise SystemExit(1 if failed else 0)
