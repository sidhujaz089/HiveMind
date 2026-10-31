#!/usr/bin/env python3
"""t1186 -- a write to a file inside a service's import closure is recorded.

WHY (workspace/rca/check-2bfc93dbf02e.md): scripts/modellease.py's mtime bumped ~2 min
before the hourly stale-service check and NOTHING recorded why. The check flagged both
guards STALE, and 'a deploy' was indistinguishable from 'an unrelated save'.
workspace/service-changes.jsonl does not exist on disk, so no write has ever been recorded.

This suite IS the decay test the RCA asked for: touch a file inside a service's import
closure and, while the writer is absent, the run is RED. A writer deleted later, never
wired up, or writing rows that do not say which file and what content, fails here.

Run with plain python3 -- pytest is not installed. No live systemctl, no model calls, no
fixed /tmp paths, no subprocess, nothing left running.
"""
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # scripts/tests/ -> repo root
sys.path.insert(0, str(ROOT / 'scripts'))
import check_stale_services as cs                 # module exists; the writer does not

UTC_RE = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')


def _write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding='utf-8')


def _rows(ledger):
    return [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]


def test_record_file_write_appends_who_what_when_and_the_content_hash():
    """The row the RCA needed: WHO wrote, WHAT file, WHEN, and the CONTENT hash."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        target = tmp / 'scripts' / 'modellease.py'
        _write(target, 'LEASE = 1\n')
        ledger = tmp / 'service-changes.jsonl'
        row = cs.record_file_write(target, actor='q16', task='t1186', ledger=ledger)
        assert ledger.is_file(), 'the ledger %s was not created' % ledger
        lines = ledger.read_text(encoding='utf-8').splitlines()
        assert len(lines) == 1, 'expected exactly one whole row, got %d: %r' % (len(lines), lines)
        got = json.loads(lines[0])
        assert got == row, 'the row returned differs from the row written: %r vs %r' % (row, lines[0])
        assert got.get('action') == 'write', 'action must be write, got %r' % (got.get('action'),)
        assert got.get('target') == str(target), 'target must be the whole path as given: %r' % (got.get('target'),)
        assert got.get('actor') == 'q16', 'the row must record WHO wrote: %r' % (got,)
        assert got.get('task') == 't1186', 'the row must record the task: %r' % (got,)
        assert UTC_RE.match(str(got.get('at', ''))), 'at is not a UTC stamp: %r' % (got.get('at'),)
        assert got.get('content') == hashlib.sha256('LEASE = 1\n'.encode('utf-8')).hexdigest(), \
            'content must be the sha256 of the WHOLE file as written: %r' % (got.get('content'),)


def test_the_hash_is_of_the_file_bytes_not_a_constant():
    """Same bytes -> same hash; two files -> two distinct targets. Kills a writer that
    stores a fixed string, and a writer that cannot tell two writes apart."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        a, b = tmp / 'a' / 'modellease.py', tmp / 'b' / 'modellease.py'
        _write(a, 'X = 1\n')
        _write(b, 'X = 1\n')
        ledger = tmp / 'service-changes.jsonl'
        ra = cs.record_file_write(a, actor='q16', task='t1186', ledger=ledger)
        rb = cs.record_file_write(b, actor='q16', task='t1186', ledger=ledger)
        assert ra['content'] == rb['content'] == hashlib.sha256(b'X = 1\n').hexdigest()
        assert ra['target'] != rb['target'], 'the two rows must name their own files'
        rows = _rows(ledger)
        assert len(rows) == 2, 'one whole row per write, got %d' % len(rows)
        assert {r['target'] for r in rows} == {str(a), str(b)}


def test_the_ledger_is_append_only_and_never_rewrites_a_recorded_line():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        target = tmp / 'modellease.py'
        _write(target, 'first\n')
        ledger = tmp / 'service-changes.jsonl'
        cs.record_file_write(target, actor='q16', task='t1186', ledger=ledger)
        before = ledger.read_text(encoding='utf-8')
        _write(target, 'second\n')
        cs.record_file_write(target, actor='claude', task='t1186', ledger=ledger)
        lines = ledger.read_text(encoding='utf-8').splitlines()
        assert len(lines) == 2, 'append-only: expected 2 rows, got %d' % len(lines)
        assert lines[0] + '\n' == before, 'a row already written was rewritten'
        assert json.loads(lines[1])['content'] == hashlib.sha256(b'second\n').hexdigest()
        assert json.loads(lines[1])['actor'] == 'claude', 'the second row records its own actor'


def test_a_touched_file_inside_the_closure_with_no_row_is_reported():
    """The RCA's decay test, mechanically: touch a service-closure file, and while the
    writer is absent it comes back UNRECORDED (red); recording the write clears it."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        entry = tmp / 'memory_guard.py'
        _write(entry, 'import scheduler_commands\n')
        _write(tmp / 'scheduler_commands.py', 'import modellease\n')
        _write(tmp / 'modellease.py', 'LEASE = 1\n')
        _write(tmp / 'unrelated.py', 'x = 1\n')
        ledger = tmp / 'service-changes.jsonl'
        touched = tmp / 'modellease.py'
        open(touched, 'a').close()                       # a touch: new mtime, same bytes
        pending = cs.unrecorded_writes([touched, tmp / 'unrelated.py'], entry, ledger=ledger)
        assert [Path(p).name for p in pending] == ['modellease.py'], \
            'a touched closure file with no ledger row must be reported, got %r' % (pending,)
        cs.record_file_write(touched, actor='q16', task='t1186', ledger=ledger)
        assert cs.unrecorded_writes([touched, tmp / 'unrelated.py'], entry, ledger=ledger) == [], \
            'after the writer records the write, nothing is outstanding'


def test_a_file_outside_the_import_closure_needs_no_row():
    """The negative half: the ledger is for SERVICE files. Flagging a file the service
    does not import is the over-reach that made the sibling-glob stale check a false
    alarm -- see check_stale_services.imported_locals' own docstring."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        entry = tmp / 'thinkpad_guard.py'
        _write(entry, 'import scheduler_commands\n')
        _write(tmp / 'scheduler_commands.py', 'import modellease\n')
        _write(tmp / 'modellease.py', 'LEASE = 1\n')
        _write(tmp / 'unrelated.py', 'x = 1\n')
        ledger = tmp / 'service-changes.jsonl'
        assert cs.unrecorded_writes([tmp / 'unrelated.py'], entry, ledger=ledger) == []
        assert cs.in_service_closure(tmp / 'modellease.py', entry) is True
        assert cs.in_service_closure(tmp / 'unrelated.py', entry) is False
        assert cs.in_service_closure(entry, entry) is True, 'the entry point is in its own closure'


def test_a_long_path_is_recorded_whole():
    """docs/skills/never-truncate-a-record: a cut target is a record that lies."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        target = tmp / ('a' * 120) / ('b' * 120) / 'modellease.py'
        _write(target, 'LEASE = 1\n')
        ledger = tmp / 'service-changes.jsonl'
        row = cs.record_file_write(target, actor='q16', task='t1186', ledger=ledger)
        assert row['target'] == str(target), 'the recorded target is shorter than the path'
        assert len(row['target']) == len(str(target))


if __name__ == '__main__':
    _tests = sorted((n, f) for n, f in list(globals().items())
                    if n.startswith('test_') and callable(f))
    _failed = []
    for _name, _fn in _tests:
        try:
            _fn()
            print('ok   ' + _name)
        except Exception as _e:                                    # noqa: BLE001 -- reported, not swallowed
            _failed.append(_name)
            print('FAIL %s: %s: %s' % (_name, type(_e).__name__, _e))
    print('%d of %d test(s) failed' % (len(_failed), len(_tests)))
    sys.exit(1 if _failed else 0)
