#!/usr/bin/env python3
"""t1040: a finding recorded with no reviewer tool call is a TOOL-LESS FINDING.

session_review.py's code treats search_tasks and commits_since as the defence against a false
deferral, and _keep writes a finding row carrying a key and a request. The console side is now
recording each tool call (t1040's console chunk). This file pins the other half: session_review
must be able to say, from the two ledgers, which findings were written by a reviewer that never
ran the check, and status() must report that count -- the surface it runs on by itself.

RED ON MAIN for the missing change: no tool_coverage() in session_review, and no
tool_less_findings key in status(). Nothing outside a temp dir is written; no model, no
subprocess, no network.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import session_review as sr  # noqa: E402

REQ = 'claude-11112222.json'
OTHER = 'claude-33334444.json'
K1 = 'aabbccddee11'
K2 = 'aabbccddee22'


def _finding(key, request=REQ, kind='deferral'):
    return {'event': 'finding', 'key': key, 'request': request, 'kind': kind}


def test_a_finding_with_no_tool_row_is_reported():
    out = sr.tool_coverage([_finding(K1)], [])
    assert [r['key'] for r in out] == [K1], out


def test_a_search_tasks_row_for_that_request_discharges_it():
    tools = [{'request': REQ, 'tool': 'search_tasks', 'args': {'query': 'zephyrquilt'}, 'hits': 2}]
    assert sr.tool_coverage([_finding(K1)], tools) == [], tools


def test_a_commits_since_row_discharges_it_too():
    tools = [{'request': REQ, 'tool': 'commits_since',
              'args': {'query': 'zephyrquilt', 'since': '2026-10-02'}, 'hits': 1}]
    assert sr.tool_coverage([_finding(K1)], tools) == [], tools


def test_a_tool_row_for_a_different_request_does_not_count():
    tools = [{'request': OTHER, 'tool': 'search_tasks', 'args': {'query': 'zephyrquilt'},
              'hits': 1}]
    assert [r['key'] for r in sr.tool_coverage([_finding(K1)], tools)] == [K1], \
        'one session tool call cannot cover another session finding'


def test_a_tool_that_is_not_a_cover_check_does_not_count():
    tools = [{'request': REQ, 'tool': 'read_file', 'args': {'path': 'x.py'}, 'hits': 1},
             {'request': REQ, 'tool': 'run_command', 'args': {'command': 'ls'}, 'hits': 1}]
    assert [r['key'] for r in sr.tool_coverage([_finding(K1)], tools)] == [K1], \
        'only the reviewer own cover checks discharge a finding'


def test_only_finding_events_are_gated():
    rows = [{'event': 'covered', 'request': REQ, 'task': 't623'},
            {'event': 'reviewed', 'request': REQ, 'findings': 0}]
    assert sr.tool_coverage(rows, []) == [], \
        'the gate is about findings; covered and reviewed events are not findings'


def test_each_uncovered_finding_is_listed_and_covered_ones_are_not():
    tools = [{'request': REQ, 'tool': 'search_tasks', 'args': {'query': 'zephyrquilt'},
              'hits': 0}]
    out = [r['key'] for r in sr.tool_coverage([_finding(K1), _finding(K2)], tools)]
    assert out == [K2], \
        'the finding whose request ran the check is discharged, the other is not: %r' % (out,)


def test_status_counts_them():
    tmp = Path(tempfile.mkdtemp(prefix='t1040-gate-'))
    led = tmp / 'ledger.jsonl'
    tools = tmp / 'tool-ledger.jsonl'
    led.write_text(json.dumps(_finding(K1)) + '\n' + json.dumps(_finding(K2)) + '\n',
                   encoding='utf-8')
    tools.write_text(json.dumps({'ts': 'now', 'request': REQ, 'key': K1, 'tool': 'search_tasks',
                                'args': {'query': 'zephyrquilt'}, 'hits': 0}) + '\n',
                     encoding='utf-8')
    sr.LEDGER, sr.TOOL_LEDGER = led, tools
    st = sr.status()
    assert st.get('tool_less_findings') == 1, \
        'status() must report the one finding with no tool row, out of two: %r' % (st,)


if __name__ == '__main__':
    _fails = 0
    for _name, _fn in sorted(list(globals().items())):
        if _name.startswith('test_') and callable(_fn):
            try:
                _fn()
                print('ok   ' + _name)
            except Exception as _e:                                # noqa: BLE001
                _fails += 1
                print('FAIL %s: %s: %s' % (_name, type(_e).__name__, _e))
    print('%d failure(s)' % _fails)
    sys.exit(1 if _fails else 0)
