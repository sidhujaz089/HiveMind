#!/usr/bin/env python3
"""t088 -- scripts/thinkpad_guard.py must own ONE command-execution path to the ThinkPad.

WHY (t088, 'Make the ThinkPad an extension of the HiveMind ... send commands and code on device
through power shell ... for anything that needs x86'): today the only code in this repository that
runs a command on the ThinkPad is two private helpers inside scripts/thinkpad_guard.py.
free_ram_gb() (line 69) pastes its own `powershell -NoLogo -Command ...` string straight into
subprocess.run (line 74), and unload() (line 110) pastes its own `ollama stop <name>` (line 115).
The ssh argv that device() builds at line 52 from SERVERS.json never leaves the file. So nothing
else -- no x86 build runner, no test runner, no Hub tile -- can send a command there.

This suite pins the ONE shape that makes the next step possible: run(cmd) is the single place that
knows how the ThinkPad is reached; every call carries a timeout; a FAILED command is distinguishable
from an empty success (never a None verdict, never a silent continue); stdout is returned WHOLE
(never-truncate-a-record); and the two existing helpers go through it, so the transport is written
once and the next consumer can reuse it.

RED ON MAIN for one reason: scripts/thinkpad_guard.py has no `run`.
No network, no model, no live ssh, no process left running: subprocess.run is replaced by a stub
that REFUSES any command that is not an ssh invocation (so a build that runs the command locally
cannot pass), and the fallback config fixture lives in a tempfile.TemporaryDirectory.
"""
import json
import subprocess
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import thinkpad_guard as tg  # noqa: E402

PROBE = 'Win32_PerfFormattedData_PerfOS_Memory'  # the real token, scripts/thinkpad_guard.py:73

FAILS = []


def check(label, cond, detail=''):
    if cond:
        print('  ok   ' + label)
    else:
        print('  FAIL ' + label + ('' if not detail else ' -- ' + str(detail)))
        FAILS.append(label)


def ssh_argv():
    """The device's real ssh argv, COMPUTED from SERVERS.json at test time -- never a stored copy,
    so the suite cannot drift away from the address/key the device is reached on."""
    try:
        _url, argv = tg.device()
    except Exception as e:  # noqa: BLE001 -- a config that cannot be read IS the finding
        check('SERVERS.json yields an ssh transport for the ThinkPad', False, f'{type(e).__name__}: {e}')
        return None
    return argv


def _ssh_only(label, out='', rc=0, err=''):
    """A stub that refuses traffic it was not written to answer: anything that is not `ssh ...`."""
    def fake(argv_, *a, **kw):
        argv_ = list(argv_)
        if not argv_ or argv_[0] != 'ssh':
            raise AssertionError(f'{label}: ran the command locally, not over ssh: {argv_[:2]}')
        return subprocess.CompletedProcess(argv_, rc, out, err)
    return fake


def test_run_exists_and_uses_the_device_ssh_transport():
    argv = ssh_argv()
    if not argv:
        return
    calls = []

    def fake(argv_, *a, **kw):
        argv_ = list(argv_)
        if argv_[0] != 'ssh':
            raise AssertionError('run() ran the command locally, not over ssh: ' + repr(argv_[:2]))
        calls.append((argv_, kw))
        return subprocess.CompletedProcess(argv_, 0, 'hello-out', 'hello-err')

    with mock.patch.object(tg.subprocess, 'run', fake):
        res = tg.run('Get-Date', timeout=30)
    joined = ' '.join(str(x) for x in calls[0][0]) if calls else ''
    check('exactly one command was run, over ssh', len(calls) == 1, len(calls))
    try:
        key = argv[argv.index('-i') + 1]
        target = next(a for a in argv if '@' in str(a))
    except (ValueError, StopIteration):
        check('the device ssh argv carries -i <key> and user@host', False, argv)
        return
    check('the key comes from SERVERS.json, not a hard-coded path', str(key) in joined, joined)
    check('the host comes from SERVERS.json, not a hard-coded address', str(target) in joined, joined)
    check('the command text is carried to the device', 'Get-Date' in joined, joined)
    t = calls[0][1].get('timeout') if calls else None
    check('every call carries a timeout', isinstance(t, (int, float)) and t > 0, calls[0][1] if calls else None)
    check('a success reports ok=True', (res or {}).get('ok') is True, res)
    check('stdout is handed back whole', (res or {}).get('out') == 'hello-out', res)
    check('stderr is handed back whole', (res or {}).get('err') == 'hello-err', res)
    check('the exit code is handed back', (res or {}).get('rc') == 0, res)


def test_run_returns_long_stdout_whole_not_sliced():
    big = 'x' * 3000 + 'TAILMARKER' + 'y' * 3000
    with mock.patch.object(tg.subprocess, 'run', _ssh_only('long-out', out=big)):
        res = tg.run('Get-Process', timeout=30)
    out = (res or {}).get('out')
    check('a long result is recorded WHOLE, never sliced (never-truncate-a-record)',
          isinstance(out, str) and len(out) == len(big) and 'TAILMARKER' in out,
          f'type={type(out).__name__} len={len(out) if isinstance(out, str) else out}')


def test_run_reports_a_failure_as_a_failure():
    with mock.patch.object(tg.subprocess, 'run', _ssh_only('fail', rc=3, err='boom: cmdlet not found')):
        res = tg.run('Get-Bogus', timeout=30)
    check('a non-zero exit is ok=False -- not True, not None', (res or {}).get('ok') is False, res)
    check('the exit code reaches the caller', (res or {}).get('rc') == 3, res)
    check('the reason reaches the caller', 'boom' in str((res or {}).get('err')), res)


def test_run_with_no_ssh_configured_records_the_reason_and_runs_nothing():
    stub = {'n': 0}

    def refuse(argv_, *a, **kw):
        stub['n'] += 1
        raise AssertionError('ran a command with no transport configured: ' + repr(list(argv_)[:2]))

    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / 'SERVERS.json'
        cfg.write_text(json.dumps({
            'devices': {'thinkpad': {}},
            'servers': {'ollama-thinkpad': {'url': 'http://127.0.0.1:1', 'device': 'thinkpad'}},
        }), encoding='utf-8')
        with mock.patch.object(tg, 'SERVERS', cfg), mock.patch.object(tg.subprocess, 'run', refuse):
            res = tg.run('Get-Date', timeout=30)
    check('no transport is invented when the device has none', stub['n'] == 0, stub)
    check('a missing transport is a recorded failure, not a silent success',
          (res or {}).get('ok') is False, res)
    check('the failure says WHY', 'ssh' in str((res or {}).get('err')).lower(), res)


def test_free_ram_gb_uses_the_shared_run_path():
    argv = ssh_argv()
    seen = []

    def recorder(*a, **kw):
        seen.append((a, kw))
        return {'ok': True, 'rc': 0, 'out': '2048\r\n', 'err': ''}

    def refuse(*a, **kw):
        raise AssertionError('free_ram_gb built its own transport instead of calling run(): '
                             + repr(list(a)[:2]))

    with mock.patch.object(tg, 'run', recorder, create=True), \
         mock.patch.object(tg.subprocess, 'run', refuse):
        got = tg.free_ram_gb(argv)
    check('free_ram_gb went through run() exactly once', len(seen) == 1, len(seen))
    flat = repr([a for a, _kw in seen] + [kw for _a, kw in seen])
    check('and asked the device the SAME memory probe as today', PROBE in flat, flat[:200])
    check('the MiB answer is still converted to GiB (2048 -> 2.0)',
          isinstance(got, float) and abs(got - 2.0) < 1e-9, got)


def test_unload_still_asks_ollama_by_name():
    seen = []

    def recorder(*a, **kw):
        seen.append((a, kw))
        return {'ok': True, 'rc': 0, 'out': '', 'err': ''}

    with mock.patch.object(tg, 'run', recorder, create=True), \
         mock.patch.object(tg.subprocess, 'run', _ssh_only('unload')):
        ok = tg.unload(['ssh', 'user@192.0.2.2'], 'gemma4:e4b')
    flat = repr([a for a, _kw in seen] + [kw for _a, kw in seen])
    check('unload sends `ollama stop <name>` -- the exact command, unchanged',
          'ollama stop gemma4:e4b' in flat, seen[:1])
    check('a clean unload reports True', ok is True, ok)


TESTS = (
    test_run_exists_and_uses_the_device_ssh_transport,
    test_run_returns_long_stdout_whole_not_sliced,
    test_run_reports_a_failure_as_a_failure,
    test_run_with_no_ssh_configured_records_the_reason_and_runs_nothing,
    test_free_ram_gb_uses_the_shared_run_path,
    test_unload_still_asks_ollama_by_name,
)


def main():
    for fn in TESTS:
        print(fn.__name__)
        try:
            fn()
        except AttributeError as e:
            check('calls tg.run(...) -- the change is absent', False, f'AttributeError: {e}')
        except AssertionError as e:
            check('aborted by the stub', False, e)
    print('FAILURES: ' + str(len(FAILS)) if FAILS else 'ALL PASS')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
