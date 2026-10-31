#!/usr/bin/env python3
"""t804 red fixture: every harness monitor event gets a row at the monitor endpoint.

CAUSE (workspace/tasks/t804--record-every-harness-monitor-event-at-the-monito.md:17, and
the sibling RCAs 64d5b943a4f0, 6de92924186c, 70128eaf86ae, 0f4b63c63a8c, 04d758d508d1):
nothing on this box records that a harness background monitor was armed, fired, retired
or left armed, so an off-flow finding quoting a 'Monitor event: "..."' notification can
never be verified, and a session's claim that it left a monitor armed can never be
checked. RED ON MAIN because scripts/monitor_events.py does not exist: verified 2026-10-09
by 'ls scripts/monitor_events.py' (No such file or directory) and by a repo-wide grep for
monitor_events, which matches only this task's file and its RCA notes. Every assertion
below that a row was written fails for that one reason -- the missing module -- not for a
bug in this file.

THE CONTRACT pinned here (from the task body line 17 and the RCA notes): one JSON object
per line, keys exactly
    session, ts, monitor_name, kind, task, script, spawn_endpoint, state
with kind in {harness-monitor, wakeup, task-supervision} and state in
{armed, fired, retired, left-armed, cancelled}. The three fields that say WHO and WHERE
-- session, monitor_name, spawn_endpoint -- are REQUIRED: an event that cannot name its
endpoint is refused at the writer with a non-zero exit and NO row written. That refusal is
the task's 'REQUIRED endpoint field' rule written where a model cannot satisfy it by
printing a line.

Stands alone -- pytest is not installed:
    python3 scripts/tests/test_monitor_events.py
Stdlib only. Writes only inside tempfile.TemporaryDirectory(). No model call, no server,
no file under workspace/ is created.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]        # scripts/tests/ -> the repo root
MODULE = REPO / 'scripts' / 'monitor_events.py'

# The row contract: exactly the eight keys the task body names.
REQUIRED_KEYS = ('session', 'ts', 'monitor_name', 'kind', 'task', 'script',
                 'spawn_endpoint', 'state')

# Real monitor titles, quoted by the findings this ledger exists to verify. Distinct
# values, so a writer that stamps one generic title on every event cannot pass.
SESSION = '6cc34e3b-0c16-4f3b-8f78-98893304830f'
NAME_A = 'Methodology and memory proposal'        # rca 64d5b943a4f0
NAME_C = 'Head-to-head rerun with the cap fixed'  # rca 70128eaf86ae

STATES = ('armed', 'fired', 'retired', 'left-armed', 'cancelled')
KINDS = ('harness-monitor', 'wakeup', 'task-supervision')


def _run(args, out):
    """Invoke the writer with --out inside the caller's temp dir; never the live file."""
    return subprocess.run([sys.executable, str(MODULE), '--out', str(out)] + list(args),
                          capture_output=True, text=True, timeout=60)


def _rows(out):
    """Every row on disk, parsed. The ledger is read back: a write is not a record."""
    return [json.loads(line) for line in
            out.read_text(encoding='utf-8').splitlines() if line.strip()]


def _arm(out, name, state='armed', extra=()):
    return _run(['--session', SESSION, '--name', name, '--state', state,
                  '--endpoint', 'harness'] + list(extra), out)


def test_arm_writes_one_row_with_every_required_field():
    """The reason this module exists: one call at the arm endpoint leaves one row on disk
    naming the session, the monitor's real title, its kind and its state."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'monitor-events.jsonl'
        r = _arm(out, NAME_A)
        assert r.returncode == 0, (
            f'writer exited {r.returncode}; stdout={r.stdout!r} stderr={r.stderr!r}')
        assert out.is_file(), f'no row file written at {out}'
        rows = _rows(out)
        assert len(rows) == 1, f'expected exactly 1 row, got {len(rows)}: {rows!r}'
        row = rows[0]
        for key in REQUIRED_KEYS:
            assert key in row, f'row is missing the required key {key!r}: {row!r}'
        assert row['session'] == SESSION, f'session not recorded verbatim: {row!r}'
        assert row['monitor_name'] == NAME_A, f'monitor_name not recorded verbatim: {row!r}'
        assert row['kind'] == 'harness-monitor', (
            f"kind must default to 'harness-monitor', got {row.get('kind')!r}")
        assert row['state'] == 'armed', f'state not recorded: {row!r}'
        assert row['spawn_endpoint'] == 'harness', f'spawn_endpoint not recorded: {row!r}'
        assert isinstance(row['task'], str), f"task must be a string: {row.get('task')!r}"
        assert isinstance(row['script'], str), f"script must be a string: {row.get('script')!r}"
        assert isinstance(row['ts'], (int, float)) and not isinstance(row['ts'], bool), \
            f'ts must be a number, got {row["ts"]!r}'


def test_rows_append_in_order_and_keep_their_distinct_names():
    """The ledger records a whole session: two monitors, two rows, in order, each keeping
    its own title. A writer that overwrites, or that stamps one generic title on every
    event, passes a shape check and fails here."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'monitor-events.jsonl'
        r1 = _arm(out, NAME_A)
        assert r1.returncode == 0, f'first call exited {r1.returncode}: {r1.stderr!r}'
        r2 = _arm(out, NAME_C, state='fired', extra=['--task', 't804'])
        assert r2.returncode == 0, f'second call exited {r2.returncode}: {r2.stderr!r}'
        rows = _rows(out)
        assert len(rows) == 2, f'expected 2 appended rows, got {len(rows)}: {rows!r}'
        assert rows[0]['monitor_name'] == NAME_A, f'row 1 name: {rows[0]!r}'
        assert rows[0]['state'] == 'armed', f'row 1 state: {rows[0]!r}'
        assert rows[1]['monitor_name'] == NAME_C, f'row 2 name: {rows[1]!r}'
        assert rows[1]['state'] == 'fired', f'row 2 state: {rows[1]!r}'
        assert rows[1]['task'] == 't804', f'row 2 task not recorded: {rows[1]!r}'
        assert rows[0]['task'] == '', f'an unset task must be the empty string: {rows[0]!r}'


def test_every_state_in_the_contract_is_accepted():
    """'left-armed' is the state the RCA at 6de92924186c asked for: a monitor left armed at
    session close. All five states must be writable, each as a real row."""
    with tempfile.TemporaryDirectory() as td:
        for state in STATES:
            out = Path(td) / f'events-{state}.jsonl'
            r = _arm(out, NAME_A, state=state)
            assert r.returncode == 0, (
                f'state {state!r} rejected: exit {r.returncode} stderr={r.stderr!r}')
            rows = _rows(out)
            assert len(rows) == 1, f'state {state!r}: expected 1 row, got {len(rows)}'
            assert rows[0]['state'] == state, (
                f'state not recorded verbatim: sent {state!r}, row {rows[0]!r}')


def test_every_kind_in_the_contract_is_accepted():
    """kind: 'harness-monitor' (this task), 'wakeup' and 'task-supervision' (the two sibling
    RCAs that point at t804). One column, three real values -- so the ledger does not have
    to be reopened per RCA."""
    with tempfile.TemporaryDirectory() as td:
        for kind in KINDS:
            out = Path(td) / f'kind-{kind}.jsonl'
            r = _arm(out, NAME_A, extra=['--kind', kind])
            assert r.returncode == 0, (
                f'kind {kind!r} rejected: exit {r.returncode} stderr={r.stderr!r}')
            assert _rows(out)[0]['kind'] == kind, f'kind not recorded: sent {kind!r}'


def test_empty_endpoint_is_refused_and_writes_nothing():
    """The whole point of the change: an event that cannot name its endpoint must not be
    allowed to enter the ledger as if it did."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'monitor-events.jsonl'
        r = _run(['--session', SESSION, '--name', NAME_A, '--state', 'armed',
                  '--endpoint', ''], out)
        assert r.returncode != 0, (
            f'an empty spawn_endpoint must exit non-zero; got 0, stdout={r.stdout!r}')
        assert not out.exists(), f'a refused event must write no row; file exists: {out}'


def test_whitespace_endpoint_is_refused_and_writes_nothing():
    """The required-field rule is a strip, not a presence check: a value of spaces names
    no more of an endpoint than an empty one does."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'monitor-events.jsonl'
        r = _run(['--session', SESSION, '--name', NAME_A, '--state', 'armed',
                  '--endpoint', '   '], out)
        assert r.returncode != 0, f'a whitespace-only endpoint must exit non-zero: {r.stdout!r}'
        assert not out.exists(), f'a refused event must write no row; file exists: {out}'


def test_empty_monitor_name_is_refused_and_writes_nothing():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'monitor-events.jsonl'
        r = _run(['--session', SESSION, '--name', '', '--state', 'armed',
                  '--endpoint', 'harness'], out)
        assert r.returncode != 0, f'an empty monitor_name must exit non-zero: {r.stdout!r}'
        assert not out.exists(), f'a refused event must write no row; file exists: {out}'


def test_empty_session_is_refused_and_writes_nothing():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'monitor-events.jsonl'
        r = _run(['--session', '', '--name', NAME_A, '--state', 'armed',
                  '--endpoint', 'harness'], out)
        assert r.returncode != 0, f'an empty session must exit non-zero: {r.stdout!r}'
        assert not out.exists(), f'a refused event must write no row; file exists: {out}'


def test_state_outside_the_contract_is_refused():
    """A writer that accepts any string cannot be reconciled against anything: the state
    column is a closed set, and 'running' is not in it."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'monitor-events.jsonl'
        r = _arm(out, NAME_A, state='running')
        assert r.returncode != 0, (
            f"state 'running' is not in the contract and must be refused; got exit 0")
        assert not out.exists(), f'a refused event must write no row; file exists: {out}'


def test_kind_outside_the_contract_is_refused():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'monitor-events.jsonl'
        r = _arm(out, NAME_A, extra=['--kind', 'cron'])
        assert r.returncode != 0, f"kind 'cron' is not in the contract: {r.stdout!r}"
        assert not out.exists(), f'a refused event must write no row; file exists: {out}'


def test_ts_defaults_to_the_live_clock():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'monitor-events.jsonl'
        before = time.time()
        r = _arm(out, NAME_A)
        assert r.returncode == 0, f'exit {r.returncode}: {r.stderr!r}'
        ts = _rows(out)[0]['ts']
        assert isinstance(ts, (int, float)) and not isinstance(ts, bool), \
            f'ts must be a number, got {ts!r}'
        assert before - 300 <= ts <= time.time() + 300, f'ts is not the wall clock: {ts!r}'


def test_explicit_ts_is_recorded_verbatim():
    """A record is never cut. Rounding 1234.5 to 1234 is a silent truncation of the one
    field that orders the ledger."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / 'monitor-events.jsonl'
        r = _arm(out, NAME_A, extra=['--ts', '1234.5'])
        assert r.returncode == 0, f'exit {r.returncode}: {r.stderr!r}'
        assert _rows(out)[0]['ts'] == 1234.5, f'ts was altered: {_rows(out)[0]!r}'


def test_default_ledger_path_is_the_workspace_endpoint():
    """The file has to be the one the task names, under workspace/ at the repo root.
    Importing the module must be side-effect free -- this test loads it by path."""
    assert MODULE.is_file(), f'{MODULE} does not exist -- that is the change'
    spec = importlib.util.spec_from_file_location('monitor_events', MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, 'DEFAULT_OUT'), 'module defines no DEFAULT_OUT'
    default = Path(str(mod.DEFAULT_OUT))
    assert default.as_posix().endswith('workspace/monitor-events.jsonl'), \
        f'DEFAULT_OUT must end at workspace/monitor-events.jsonl, got {str(mod.DEFAULT_OUT)!r}'
    assert str(default).startswith(str(REPO)), \
        f'DEFAULT_OUT must live under the repository, got {mod.DEFAULT_OUT!r}'


def main():
    tests = sorted((name, fn) for name, fn in globals().items()
                   if name.startswith('test_') and callable(fn))
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'PASS {name}')
        except Exception as exc:  # noqa: BLE001 -- a test that dies is a fail, printed whole
            failed += 1
            print(f'FAIL {name}: {type(exc).__name__}: {exc}')
    print(f'{len(tests) - failed}/{len(tests)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
