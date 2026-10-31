#!/usr/bin/env python3
"""t1251: scripts/scoped_batch.py must record one dispatch row per item to dispatch-loop.jsonl.

RED ON MAIN FOR ONE REASON: scoped_batch.py writes nothing to disk. Its dispatch loop runs
coder.py per item (scripts/scoped_batch.py:214-221), prints a summary to stdout
(scripts/scoped_batch.py:223-226) and returns 0 (scripts/scoped_batch.py:227). No row reaches
workspace/dispatch-loop.jsonl, so a REJECTED item leaves no record at all -- which is why the
RCA's deferral ("I'll review those next") had no anchor (workspace/rca/daf4c7bbe432.md).

No model call, no socket, no live ledger: every path is a tempfile and the child is a patched
subprocess.run. Run: python3 scripts/tests/test_scoped_batch_dispatch_rows.py
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import scoped_batch as sb  # noqa: E402

REAL_LEDGER = ROOT / 'workspace' / 'dispatch-loop.jsonl'
FIRST_ID = sb.ITEMS[0]['id']         # 't155-a'     -- scripts/scoped_batch.py:48
SECOND_ID = sb.ITEMS[2]['id']        # 'mcp-caller' -- scripts/scoped_batch.py:101
UTC = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
REQUIRED = {'at', 'source', 'item_id', 'model', 'attempts', 'exit', 'outcome',
            'rejection_reason', 'log'}
CHILD_WORDS = ("FAILED scripts/tests/test_workspace_mcp.py::test_caller_reaches_tool_row - "
               "AssertionError: caller recorded as 'unknown', expected 'admin-bearer'")
calls = []


def test_api_exists():
    for name in ('DISPATCH_LEDGER', 'JOBLOGS', 'rejection_reason', 'dispatch_row',
                 'write_dispatch_rows'):
        assert getattr(sb, name, None) is not None, (
            'scripts/scoped_batch.py has no %s -- that is the change this task asks for' % name)


def test_rejection_reason_is_the_childs_whole_output():
    reason = sb.rejection_reason
    assert reason(0, 'ACCEPTED on attempt 1: target suite green') == '', \
        'a delivered run records no reason'
    got = reason(3, CHILD_WORDS)
    assert isinstance(got, str) and got.strip(), 'the reason must be a non-empty string: %r' % (got,)
    assert CHILD_WORDS in got, 'the reason must carry the child own words verbatim: %r' % (got,)
    long_out = '  ... collected\n' * 900 + CHILD_WORDS
    r2 = reason(2, long_out)
    assert CHILD_WORDS in r2, 'reason was cut at the tail: the failure line is the last line'
    assert '... collected' in r2, 'reason was cut at the head too: record the whole output'
    r3 = reason(1, '')
    assert isinstance(r3, str) and r3.strip(), 'a silent failure still needs a reason'
    assert '1' in r3, 'the reason must name the exit code when the child said nothing: %r' % (r3,)


def test_dispatch_row_carries_the_values_a_reviewer_would_check():
    with tempfile.TemporaryDirectory() as td:
        log = Path(td) / (FIRST_ID + '--gemma4-26b--1.log')
        row = sb.dispatch_row(sb.ITEMS[0], model='gemma4:26b', attempts=3, exit_code=3,
                              output=CHILD_WORDS, log_path=log)
        missing = sorted(REQUIRED - set(row))
        assert not missing, 'row is missing fields: %s' % (missing,)
        assert row['item_id'] == FIRST_ID, 'item_id must be the real ITEMS id: %r' % (row['item_id'],)
        assert row['model'] == 'gemma4:26b', row['model']
        assert row['attempts'] == 3, 'attempts must be the --attempts dispatched: %r' % (row['attempts'],)
        assert row['exit'] == 3, row['exit']
        assert row['outcome'] == 'rejected', 'expected the exact value rejected: %r' % (row['outcome'],)
        assert row['source'] == 'scoped_batch', row['source']
        assert UTC.match(row['at']), 'at must be a UTC second stamp: %r' % (row['at'],)
        assert str(log) in str(row['log']), 'log must point at the per-item log: %r' % (row['log'],)
        assert CHILD_WORDS in row['rejection_reason'], row['rejection_reason']
        ok = sb.dispatch_row(sb.ITEMS[2], model='gemma4:26b', attempts=1, exit_code=0,
                             output='ACCEPTED on attempt 1: target suite green', log_path=log)
        assert ok['item_id'] == SECOND_ID, ok['item_id']
        assert ok['outcome'] == 'accepted', 'expected the exact value accepted: %r' % (ok['outcome'],)
        rr = ok['rejection_reason']
        assert rr == '', 'a delivered run records no reason: %r' % (rr,)
        assert ok['attempts'] == 1, ok['attempts']


def test_write_appends_whole_rows_and_never_rewrites_the_ledger():
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / 'nested' / 'dispatch-loop.jsonl'
        first = {'at': '2026-10-16T00:00:00Z', 'source': 'scoped_batch', 'item_id': FIRST_ID,
                 'model': 'gemma4:26b', 'attempts': 3, 'exit': 3, 'outcome': 'rejected',
                 'rejection_reason': "caller recorded as 'unknown'", 'log': 'x.log'}
        sb.write_dispatch_rows(led, [first])
        sb.write_dispatch_rows(led, [dict(first, item_id=SECOND_ID)])
        lines = [l for l in led.read_text(encoding='utf-8').splitlines() if l.strip()]
        assert len(lines) == 2, 'append-only: expected 2 rows, got %d' % len(lines)
        assert json.loads(lines[0]) == first, 'the first row was overwritten, not appended'
        back = json.loads(lines[1])
        assert back['item_id'] == SECOND_ID, back['item_id']
        assert back['rejection_reason'] == "caller recorded as 'unknown'", back['rejection_reason']
        assert back['outcome'] == 'rejected', back['outcome']
        sb.write_dispatch_rows(led, [{'at': 'x', 'note': 'naive - tokyo 30b ok'}])
        last = json.loads(led.read_text(encoding='utf-8').splitlines()[-1])
        assert last['note'] == 'naive - tokyo 30b ok', last['note']


class _FakeCompleted:
    def __init__(self, rc):
        self.returncode = rc


def _fake_run(argv, **kw):
    """Stand-in for the coder.py child: writes its own words to the log handle, exits 3."""
    calls.append(list(argv))
    out = kw.get('stdout')
    if hasattr(out, 'write'):
        try:
            out.write(CHILD_WORDS.encode('utf-8') + b'\n')
        except TypeError:
            out.write(CHILD_WORDS + '\n')
        out.flush()
    return _FakeCompleted(3)


def test_main_records_a_row_for_the_item_it_dispatches():
    """The defect: a REJECTED item leaves no ledger row. On main, main() writes no file at all."""
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / 'dispatch-loop.jsonl'
        logs = Path(td) / 'dispatch'
        saved = (sys.argv, sb.endpoint, sb.default_model, sb.preflight, sb.dirty_for,
                 sb.subprocess.run, sb.DISPATCH_LEDGER, sb.JOBLOGS)
        del calls[:]
        sys.argv = ['scoped_batch.py', '--only', FIRST_ID]
        sb.endpoint = lambda: 'http://127.0.0.1:9/preview'
        sb.default_model = lambda: 'gemma4:26b'
        sb.preflight = lambda model: (True, 'fake preflight: the network is patched out')
        sb.dirty_for = lambda paths: []
        sb.subprocess.run = _fake_run
        sb.DISPATCH_LEDGER = ledger
        sb.JOBLOGS = logs
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = sb.main()
        finally:
            (sys.argv, sb.endpoint, sb.default_model, sb.preflight, sb.dirty_for,
             sb.subprocess.run, sb.DISPATCH_LEDGER, sb.JOBLOGS) = saved
        out = buf.getvalue()
        assert rc == 0, 'main() must still return 0: %r' % (rc,)
        assert ledger.is_file(), 'no ledger was written at all -- the deferral has no anchor'
        rows = [json.loads(l) for l in ledger.read_text(encoding='utf-8').splitlines() if l.strip()]
        assert len(rows) == 1, 'one item dispatched, one row expected: %r' % (rows,)
        r = rows[0]
        assert r['item_id'] == FIRST_ID, r['item_id']
        assert r['model'] == 'gemma4:26b', r['model']
        assert r['attempts'] == 3, 'the default --attempts is 3 (scripts/scoped_batch.py:186)'
        assert r['exit'] == 3, r['exit']
        assert r['outcome'] == 'rejected', r['outcome']
        assert 'unknown' in r['rejection_reason'], \
            'the reason must quote the child, not the exit code: %r' % (r['rejection_reason'],)
        assert Path(r['log']).is_file(), 'the log field must point at a file that exists: %r' % (r['log'],)
        assert FIRST_ID in Path(r['log']).name, 'the log name must carry the item id: %r' % (r['log'],)
        assert CHILD_WORDS in Path(r['log']).read_text(encoding='utf-8'), 'the log must hold the child output'
        assert len(calls) == 1, 'exactly one child dispatch for one item: %r' % (calls,)
        assert sb.ITEMS[0]['target'] in calls[0], \
            'the recorded row must describe the dispatch that ran: %r' % (calls[0],)
        assert '--model' in calls[0] and 'gemma4:26b' in calls[0], calls[0]
        assert 'not delivered (exit 3)' in out, 'the existing stdout summary must survive: ' + out
        assert 'accepted and written' not in out, out
        assert 'Nothing is merged. Waiting for review' in out, out[-400:]


def test_the_live_ledger_is_never_touched_by_this_suite():
    if not REAL_LEDGER.is_file():
        return
    before = os.stat(REAL_LEDGER)
    with tempfile.TemporaryDirectory() as td:
        sb.write_dispatch_rows(Path(td) / 'l.jsonl', [{'at': '2026-10-16T00:00:00Z'}])
    after = os.stat(REAL_LEDGER)
    assert (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), \
        'an oracle must never write the live ledger: workspace/dispatch-loop.jsonl'


def _run():
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith('test_') and callable(v)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print('PASS ' + name)
        except Exception:
            failed.append(name)
            print('FAIL ' + name + ': ' + traceback.format_exc().strip().splitlines()[-1])
    print('%d/%d passed' % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run())
