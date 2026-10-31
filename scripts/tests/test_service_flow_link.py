#!/usr/bin/env python3
'''t764 -- a permanent service cannot be registered, stopped or removed off the Flow chart.

scripts/check_stale_services.py is the repo's existing service census: it enumerates running
systemd user units and reports the ones running code older than the process. It has no
registration/removal endpoint, so a permanent service can be created, exposed, stopped,
uninstalled and deleted with no chart node and no ledger row -- installing, exposing, stopping
and uninstalling Open WebUI left no record at all (workspace/rca/39cc3df02e11.md), and the
ollama.service install's only record is pasted terminal output (workspace/rca/50c99d891199.md).

RED ON MAIN FOR ONE REASON: scripts/check_stale_services.py has no service-mutation endpoint.
Verified 2026-10-09 by grep -n "def " over the file: proc_table, sh, user_services, started_at,
imported_locals, newest_change, self_waiting_processes, _ancestors, in_flight_dispatches,
stray_repo_processes, main -- no record_service_change, no chart_service_nodes, no SERVICE_LEDGER,
no register verb; and workspace/service-changes.jsonl does not exist. Every test below fails on
that absence, and each assertion is the behaviour the endpoint must have.

No live state is touched: every mutation passes --ledger into a temp dir, the census (a run with
no arguments) is never invoked, and the real workspace/service-changes.jsonl is only counted.
'''
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts' / 'check_stale_services.py'
DEFAULT_LEDGER = ROOT / 'workspace' / 'service-changes.jsonl'
TIMEOUT = 60
TIME_FORMAT = '%Y-%m-%dT%H:%M:%SZ'
REQUIRED_KEYS = {'at', 'actor', 'action', 'target', 'task', 'flow_node'}
VALID_NODE = 'svc_hub'          # a real services[] id in FLOW.json (read via json.load 2026-10-09)
NOT_A_NODE = 'svc_not_a_service_on_the_chart'
CHART_SERVICE_IDS = {'svc_hub', 'svc_console', 'svc_chat', 'svc_terminal', 'svc_tools', 'svc_dsh',
                     'svc_apprepo', 'svc_mcp', 'svc_tunnel', 'svc_manager', 'svc_swift', 'svc_key'}

sys.path.insert(0, str(ROOT / 'scripts'))
import check_stale_services as m


def rows(path):
    p = Path(path)
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]


def cli(args, ledger=None):
    cmd = [sys.executable, str(SCRIPT)] + list(args)
    if ledger is not None:
        cmd += ['--ledger', str(ledger)]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)


def test_the_endpoint_exists_in_the_census_module():
    assert hasattr(m, 'record_service_change'), ('scripts/check_stale_services.py has no '
        'record_service_change(): nothing records a permanent-service mutation today')


def test_record_appends_one_row_with_every_required_field():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / 'service-changes.jsonl'
        row = m.record_service_change('create', 'open-webui.service', actor='session', task='t764',
                                      flow_node=VALID_NODE, ledger=ledger)
        assert set(row) == REQUIRED_KEYS, sorted(row)
        assert row['action'] == 'create', row
        assert row['target'] == 'open-webui.service', row
        assert row['actor'] == 'session', row
        assert row['task'] == 't764', row
        assert row['flow_node'] == VALID_NODE, row
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', str(row['at'])), row['at']
        datetime.strptime(str(row['at']), TIME_FORMAT)
        got = rows(ledger)
        assert len(got) == 1, got
        assert got[0]['target'] == 'open-webui.service', got
        assert got[0]['flow_node'] == VALID_NODE, got


def test_the_ledger_is_append_only_and_keeps_the_order():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / 'service-changes.jsonl'
        m.record_service_change('create', 'open-webui.service', actor='session', task='t764',
                                flow_node=VALID_NODE, ledger=ledger)
        m.record_service_change('remove', 'open-webui.service', actor='jas', task='t772',
                                flow_node=VALID_NODE, ledger=ledger)
        got = rows(ledger)
        assert [g['action'] for g in got] == ['create', 'remove'], got
        assert [g['actor'] for g in got] == ['session', 'jas'], got
        assert [g['task'] for g in got] == ['t764', 't772'], got
        assert [g['target'] for g in got] == ['open-webui.service', 'open-webui.service'], got


def test_a_mutation_naming_no_flow_node_is_refused_and_records_nothing():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / 'service-changes.jsonl'
        try:
            m.record_service_change('create', 'open-webui.service', actor='session', task='t764',
                                    flow_node='', ledger=ledger)
        except ValueError as exc:
            assert 'open-webui.service' in str(exc), str(exc)
            assert 'flow node' in str(exc).lower(), str(exc)
        else:
            raise AssertionError('record_service_change accepted create/open-webui.service '
                                 'with no flow node: the gate the task exists for is absent')
        assert rows(ledger) == [], rows(ledger)


def test_a_flow_node_must_be_a_node_on_the_real_chart():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / 'service-changes.jsonl'
        try:
            m.record_service_change('stop', 'open-webui.service', actor='session', task='t764',
                                    flow_node=NOT_A_NODE, ledger=ledger)
        except ValueError as exc:
            assert NOT_A_NODE in str(exc), str(exc)
            assert 'chart' in str(exc).lower(), str(exc)
        else:
            raise AssertionError('record accepted ' + NOT_A_NODE + ', which is not a services[] '
                                 'id in FLOW.json: the gate is not consulting the chart')
        assert rows(ledger) == [], rows(ledger)


def test_the_chart_nodes_are_read_from_the_real_flow_json():
    nodes = set(m.chart_service_nodes())
    assert CHART_SERVICE_IDS <= nodes, str(sorted(nodes))
    assert NOT_A_NODE not in nodes, str(sorted(nodes))


def test_the_cli_register_verb_records_the_mutation():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / 'service-changes.jsonl'
        r = cli(['register', 'create', 'open-webui.service', '--flow-node', VALID_NODE,
                 '--actor', 'session', '--task', 't764'], ledger)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        got = rows(ledger)
        assert len(got) == 1, (got, r.stdout, r.stderr)
        assert got[0]['action'] == 'create', got
        assert got[0]['target'] == 'open-webui.service', got
        assert got[0]['flow_node'] == VALID_NODE, got
        assert got[0]['task'] == 't764', got
        assert 'open-webui.service' in (r.stdout + r.stderr), (r.stdout, r.stderr)


def test_the_cli_refuses_a_mutation_with_no_flow_node_and_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / 'service-changes.jsonl'
        r = cli(['register', 'create', 'open-webui.service', '--actor', 'session',
                 '--task', 't764'], ledger)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        text = r.stdout + r.stderr
        assert 'open-webui.service' in text, (r.stdout, r.stderr)
        assert 'flow node' in text.lower(), (r.stdout, r.stderr)
        assert rows(ledger) == [], rows(ledger)


def test_a_blank_flow_node_is_refused_like_a_missing_one():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / 'service-changes.jsonl'
        r = cli(['register', 'stop', 'open-webui.service', '--flow-node', ' ', '--actor', 'session',
                 '--task', 't764'], ledger)
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert 'flow node' in (r.stdout + r.stderr).lower(), (r.stdout, r.stderr)
        assert rows(ledger) == [], rows(ledger)


def test_an_unknown_action_is_not_silently_accepted():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / 'service-changes.jsonl'
        r = cli(['register', 'launch', 'open-webui.service', '--flow-node', VALID_NODE,
                 '--task', 't764'], ledger)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        text = (r.stdout + r.stderr).lower()
        assert 'create' in text and 'stop' in text and 'remove' in text, (r.stdout, r.stderr)
        assert rows(ledger) == [], rows(ledger)


def test_a_read_only_action_is_not_gated_and_records_no_row():
    with tempfile.TemporaryDirectory() as d:
        ledger = Path(d) / 'service-changes.jsonl'
        r = cli(['register', 'status', 'open-webui.service', '--actor', 'session',
                 '--task', 't764'], ledger)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert rows(ledger) == [], rows(ledger)


def test_the_default_ledger_is_the_repo_ledger():
    assert Path(m.SERVICE_LEDGER).name == 'service-changes.jsonl', m.SERVICE_LEDGER
    assert Path(m.SERVICE_LEDGER).parent.name == 'workspace', m.SERVICE_LEDGER


def test_a_refused_mutation_never_creates_the_live_ledger():
    before = len(rows(DEFAULT_LEDGER))
    r = subprocess.run([sys.executable, str(SCRIPT), 'register', 'create', 'open-webui.service',
                        '--actor', 'session', '--task', 't764'],
                       capture_output=True, text=True, timeout=TIMEOUT)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert len(rows(DEFAULT_LEDGER)) == before, 'a refused mutation wrote the live ledger'


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
    sys.exit(1 if failed else 0)
