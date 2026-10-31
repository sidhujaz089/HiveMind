#!/usr/bin/env python3
"""t1040: every session-reviewer tool call is recorded as a ledger row.

The reviewer's prompt tells it to run search_tasks and commits_since before reporting a deferral,
and session_review.py's own code calls those checks its main defence against a false deferral.
Nothing at the console endpoint records that the check ran, so every cover-miss RCA must re-argue
from first principles whether it fired.

RED ON MAIN for the missing change: console has no TOOL_LEDGER constant and the two tools write no
row. Nothing outside a temp dir is written; no model, no network, no process left running.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'projects' / 'local-inference')]
import console  # noqa: E402

# Captured at import, BEFORE any test redirects the path. Without this the path assertion reads a
# temp dir left by an earlier test and stays red even after a correct build.
DEFAULT_TOOL_LEDGER = str(getattr(console, 'TOOL_LEDGER', ''))

QUERY_A = 'zephyrquilt'
QUERY_B = 'marbledglint'
SINCE = '2026-01-02'
REQ = 'claude-11112222.json'
KEY = 'abcd1234ef56'


def _setup():
    """A fake task repo under a temp dir, the ledger redirected into it, env set for the run."""
    tmp = Path(tempfile.mkdtemp(prefix='t1040-'))
    tasks = tmp / 'repo' / 'workspace' / 'tasks'
    (tasks / 'done').mkdir(parents=True)
    (tasks / 't900--alpha.md').write_text(
        'id: t900\ntitle: Alpha\nstatus: open\n\nthe %s knob is open\n' % QUERY_A,
        encoding='utf-8')
    (tasks / 'done' / 't901--beta.md').write_text(
        'id: t901\ntitle: Beta\nstatus: done\n\n%s and %s both live here\n' % (QUERY_A, QUERY_B),
        encoding='utf-8')
    console.REPO = tmp / 'repo'
    console.TOOL_LEDGER = tmp / 'tool-ledger.jsonl'
    os.environ['SESSION_REVIEW_REQUEST'] = REQ
    os.environ['SESSION_REVIEW_KEY'] = KEY
    return tmp


def _rows():
    p = Path(console.TOOL_LEDGER)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding='utf-8', errors='replace').splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def test_ledger_path_is_the_named_one():
    assert DEFAULT_TOOL_LEDGER, \
        'console.TOOL_LEDGER is not defined: no tool-ledger path to append rows to (t1040)'
    p = Path(DEFAULT_TOOL_LEDGER)
    assert p.name == 'tool-ledger.jsonl', p.name
    assert p.parent.name == 'session-reviews', str(p.parent)


def test_each_call_appends_one_row():
    _setup()
    console.tool_search_tasks({'query': QUERY_A})
    console.tool_search_tasks({'query': QUERY_B})
    rows = _rows()
    assert len(rows) == 2, 'expected exactly one row per tool call, got %d' % len(rows)
    assert [r.get('tool') for r in rows] == ['search_tasks', 'search_tasks'], rows
    assert [r.get('args') for r in rows] == [{'query': QUERY_A}, {'query': QUERY_B}], rows
    for r in rows:
        assert r.get('ts'), 'row carries no ts: %r' % (r,)
        assert r.get('request') == REQ, r
        assert r.get('key') == KEY, r


def test_the_hit_count_recorded_is_the_count_of_matches():
    _setup()
    console.tool_search_tasks({'query': QUERY_A})   # both fake tasks carry it
    console.tool_search_tasks({'query': QUERY_B})   # one of the two does
    hits = [r.get('hits') for r in _rows()]
    assert hits == [2, 1], 'hits must be the query own match count, not a constant: %r' % (hits,)


def test_commits_since_records_its_date_argument_and_its_hits():
    _setup()
    console.tool_commits_since({'query': QUERY_A, 'since': SINCE})
    rows = [r for r in _rows() if r.get('tool') == 'commits_since']
    assert len(rows) == 1, rows
    assert rows[0].get('args') == {'query': QUERY_A, 'since': SINCE}, \
        'the since= date is the fact a date-window bug turns on: %r' % (rows[0],)
    assert rows[0].get('hits') == 0, \
        'a directory that is not a git repo finds no commit: %r' % (rows[0],)


def test_a_refused_call_is_recorded():
    _setup()
    out = console.tool_commits_since({'query': QUERY_A})   # no since= -> refused
    assert str(out).startswith('REFUSED:'), out
    rows = [r for r in _rows() if r.get('tool') == 'commits_since']
    assert len(rows) == 1, 'a refused call is still a call the reviewer made: %r' % (rows,)
    assert rows[0].get('args') == {'query': QUERY_A}, \
        'record only the arguments the call supplied: %r' % (rows[0],)


def test_a_tool_that_was_not_called_leaves_no_row():
    _setup()
    console.tool_search_tasks({'query': QUERY_A})
    tools = sorted({r.get('tool') for r in _rows()})
    assert tools == ['search_tasks'], \
        'a search_tasks call must not mint a commits_since row: %r' % (tools,)


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
