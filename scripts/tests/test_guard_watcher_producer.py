#!/usr/bin/env python3
"""t1075 -- refuse a backgrounded watcher whose completion token has no live producer.

Why this suite exists (workspace/rca/931d9518ab93.md; the task body at
workspace/tasks/t1075--refuse-a-backgrounded-watcher-whose-completion-t.md): the monitor
armed 2026-08-26 18:03:19Z exits only when $S/probe2.done appears; the probe was killed at
19:29:13Z, which does not write that file; at 20:19:21Z the SAME sentinel was re-armed for a
run whose producer had been dead 50 minutes, and the watcher ran 138 minutes 18 s before
anyone noticed. Kill-of-job and retire-of-watcher are unlinked, and the sentinel is
per-round, not per-run, so a watcher can outlive -- and be re-armed against -- a dead job.

This pins the arm guard in the one existing file on this box that sees a watcher arm BEFORE
it runs: the PreToolUse hook scripts/hooks/guard_process_waits.py. The hook is driven the way
the harness drives it -- a JSON tool_use on stdin, exit code and text on the way out -- and
the ledger is READ BACK from disk, so a guard that answers in memory is a failure.

RED ON MAIN for one reason: the hook has no arm guard today, so "register a producer, kill
it, arm a watcher on its sentinel" exits 0 and writes nothing. Verified 2026-10-12 from
.claude/settings.json: PreToolUse has matchers "Write|Edit" and "Bash|mcp__local__bash" only
-- no matcher names Monitor -- and no module in the repo writes an arm ledger.

Runs standalone -- pytest is not installed:
    python3 scripts/tests/test_guard_watcher_producer.py
Stdlib only. Writes only inside tempfile.TemporaryDirectory(). No model call, no live
server, no process left running: subprocess.run only, every one with a timeout, no sleep.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]        # scripts/tests/ -> the repo root
HOOK = REPO / 'scripts' / 'hooks' / 'guard_process_waits.py'

# The two sentinels the RCA names, used as DISTINCT values: a guard that keys on the
# backgrounding flag alone, or that blocks every arm, cannot pass both of those cases.
SENTINEL_A = '/home/jas/ai-workspace/workspace/runs/6cc34e3b/probe2.done'
SENTINEL_B = '/home/jas/ai-workspace/workspace/runs/6cc34e3b/probe.done'

ARM_A = ('nohup bash -c "while [ ! -e ' + SENTINEL_A + ' ]; do sleep 30; done; echo ready" '
         '> /dev/null 2>&1 &')
ARM_B = ('nohup bash -c "while [ ! -e ' + SENTINEL_B + ' ]; do sleep 30; done; echo ready" '
         '> /dev/null 2>&1 &')


def _hook(led, command, extra=None, tool='Bash'):
    """Invoke the hook exactly as the harness does: a tool_use JSON on stdin."""
    env = dict(os.environ)
    env['WATCHER_ARM_LEDGER'] = str(led)          # the ledger is always a temp path
    for k, v in (extra or {}).items():
        env[k] = str(v)
    payload = json.dumps({'tool_name': tool, 'tool_input': {'command': command}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True,
                          text=True, timeout=60, env=env, cwd=str(REPO))


def _rows(led):
    """Every row written to the ledger, parsed. A write is not a record: the file is read back."""
    assert led.is_file(), 'no arm ledger written at ' + str(led)
    out = []
    for line in led.read_text(encoding='utf-8').splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def _record(led, sentinel, pid):
    """Register a producer for a sentinel. A session knows the pid only after the launch --
    that is why the RCA's transcript types 'echo $!' -- so it is handed in as
    WATCHER_ARM_RECORD=<sentinel>:<pid>, and the hook mints the run's completion token."""
    r = _hook(led, 'echo $!', {'WATCHER_ARM_RECORD': sentinel + ':' + str(pid)})
    assert r.returncode == 0, (
        'registering a producer must exit 0; got exit ' + str(r.returncode) +
        ' stdout=' + repr(r.stdout) + ' stderr=' + repr(r.stderr))
    rows = _rows(led)
    assert len(rows) == 1, 'registering one producer must write exactly 1 row, got ' + repr(rows)
    row = rows[0]
    assert row.get('sentinel_path') == sentinel, 'sentinel not recorded verbatim: ' + repr(row)
    assert row.get('producer_pid') == pid, 'producer_pid not recorded: ' + repr(row)
    assert row.get('token'), 'a producer registration must mint a completion token: ' + repr(row)
    return row


def _dead_pid():
    """A pid that is provably not running: a child this test started and has already reaped.
    subprocess.run reaps it, so os.kill(pid, 0) raises -- a real dead producer, not a fake."""
    p = subprocess.run([sys.executable, '-c', 'pass'], capture_output=True, timeout=30)
    assert p.returncode == 0, 'probe launcher exited ' + str(p.returncode) + ': ' + repr(p.stderr)
    return p.pid


def _refused(p):
    """Convention-agnostic refusal: a non-zero exit, or a deny decision on stdout."""
    return p.returncode != 0 or '"deny"' in p.stdout


def _allowed(p):
    return p.returncode == 0 and '"deny"' not in p.stdout


def _text(p):
    return (p.stdout or '') + (p.stderr or '')


def test_a_dead_producer_blocks_an_arm_on_its_sentinel():
    """The RCA's exact sequence: the probe is killed, and a watcher is armed on the sentinel
    only that probe writes. Today that arm is allowed and nothing sees it for 138 minutes."""
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / 'watcher-arms.jsonl'
        dead = _dead_pid()
        _record(led, SENTINEL_A, dead)
        p = _hook(led, ARM_A)
        assert _refused(p), (
            'arming a watcher on a sentinel whose only registered producer is dead must be '
            'refused; it was allowed. exit=' + str(p.returncode) + ' out=' + _text(p))


def test_the_refusal_names_the_completion_token_and_the_dead_pid():
    """The RCA's 'loudly': a refusal that does not name the token, the sentinel and the dead
    pid cannot be acted on by whoever reads the transcript. All three, verbatim."""
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / 'watcher-arms.jsonl'
        dead = _dead_pid()
        producer = _record(led, SENTINEL_A, dead)
        p = _hook(led, ARM_A)
        assert _refused(p), 'expected a refusal, got exit ' + str(p.returncode)
        out = _text(p)
        assert SENTINEL_A in out, 'the refusal must name the sentinel, got: ' + repr(out)
        assert str(dead) in out, 'the refusal must name the dead producer pid, got: ' + repr(out)
        assert producer['token'] in out, (
            'the refusal must name the completion token it is refusing for, got: ' + repr(out))


def test_a_refused_arm_writes_a_row_the_next_reader_can_see():
    """The task's own red fixture: 'the refusal row is written'. Read off disk, not returned.
    And a refused arm must not also be recorded as armed -- that is a lie in the ledger."""
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / 'watcher-arms.jsonl'
        dead = _dead_pid()
        producer = _record(led, SENTINEL_A, dead)
        p = _hook(led, ARM_A)
        assert _refused(p), 'expected a refusal, got exit ' + str(p.returncode)
        rows = _rows(led)
        refused = [x for x in rows if x.get('state') == 'refused']
        assert len(refused) == 1, 'the refusal must be recorded as exactly one row: ' + repr(rows)
        assert refused[0].get('sentinel_path') == SENTINEL_A, repr(refused[0])
        assert refused[0].get('producer_pid') == dead, repr(refused[0])
        assert refused[0].get('token') == producer['token'], repr(refused[0])
        assert not [x for x in rows if x.get('state') == 'armed'], (
            'a refused arm must not be recorded as armed: ' + repr(rows))


def test_a_sentinel_with_no_registered_producer_is_allowed():
    """The guard is a join, not a ban: the overwhelming majority of arms must go through, and
    an allowed arm is still recorded, so the ledger can answer 'was this ever armed?'."""
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / 'watcher-arms.jsonl'
        p = _hook(led, ARM_A)
        assert _allowed(p), 'an arm with no registered producer must be allowed: exit=' + \
            str(p.returncode) + ' out=' + _text(p)
        rows = _rows(led)
        armed = [x for x in rows if x.get('state') == 'armed']
        assert len(armed) == 1, 'an allowed arm must write exactly one armed row: ' + repr(rows)
        assert armed[0].get('sentinel_path') == SENTINEL_A, repr(armed[0])
        assert armed[0].get('token'), 'an armed row must carry the per-run token: ' + repr(armed[0])


def test_a_live_producer_does_not_block_the_arm():
    """Negative control, and the one that kills a blanket refusal: with the producer alive --
    this test's own pid, which is alive by construction -- the arm is allowed."""
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / 'watcher-arms.jsonl'
        _record(led, SENTINEL_A, os.getpid())
        p = _hook(led, ARM_A)
        assert _allowed(p), 'a live producer must not block the arm: exit=' + str(p.returncode) + \
            ' out=' + _text(p)


def test_a_dead_producer_does_not_block_a_different_sentinel():
    """The join is PER SENTINEL. A dead producer on probe2.done must not stop a watcher armed
    on probe.done -- a guard that blocks on any death fails here and would be routed around
    with --no-verify inside a week."""
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / 'watcher-arms.jsonl'
        _record(led, SENTINEL_A, _dead_pid())
        p = _hook(led, ARM_B)
        assert _allowed(p), 'a dead producer on another sentinel must not block this arm: exit=' + \
            str(p.returncode) + ' out=' + _text(p)
        assert not [x for x in _rows(led) if x.get('state') == 'refused'], \
            'a refused arm on an unrelated sentinel was written: ' + repr(_rows(led))


def test_retiring_the_stale_watcher_unblocks_the_sentinel():
    """The RCA's second half: kill-of-job and retire-of-watcher are unlinked. Retirement is
    the unblock -- it writes a row, and only then is a fresh arm on that sentinel allowed."""
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / 'watcher-arms.jsonl'
        dead = _dead_pid()
        producer = _record(led, SENTINEL_A, dead)
        assert _refused(_hook(led, ARM_A)), 'the arm before retirement must be refused'
        r = _hook(led, 'true', {'WATCHER_ARM_RETIRE': producer['token']})
        assert r.returncode == 0, 'retiring must exit 0: exit=' + str(r.returncode) + \
            ' out=' + _text(r)
        rows = _rows(led)
        assert any(x.get('state') == 'retired' for x in rows), \
            'retiring a watch must leave a retired row: ' + repr(rows)
        p = _hook(led, ARM_A)
        assert _allowed(p), 'after the stale watch is retired a new arm must be allowed: exit=' + \
            str(p.returncode) + ' out=' + _text(p)


def test_completion_tokens_are_per_run_not_per_round():
    """The RCA's cause, stated as the property that fixes it: the sentinel is per-round, the
    token must be per-RUN. Two producer runs on the SAME sentinel must carry DISTINCT tokens,
    or the ledger cannot tell the 18:03 arm from the 20:19 one -- which is why nothing in this
    session could see the watcher outliving its producer."""
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / 'watcher-arms.jsonl'
        first = _record(led, SENTINEL_A, os.getpid())
        second = _record(led, SENTINEL_A, os.getpid())
        assert first['token'] and second['token'], \
            'each producer run needs a token: ' + repr([first, second])
        assert first['token'] != second['token'], (
            'two producer runs on one sentinel must get distinct per-run tokens, got ' +
            repr([first['token'], second['token']]))


def test_a_non_bash_tool_use_is_passed_through():
    """The hook guards Bash today (settings.json matchers). It must not start refusing the
    other tools, and it must not write ledger rows for them: a guard that mis-fires on
    unrelated traffic is a guard that gets disabled."""
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / 'watcher-arms.jsonl'
        p = _hook(led, 'ls', tool='Read')
        assert _allowed(p), 'a non-Bash tool_use must not be refused: exit=' + str(p.returncode) + \
            ' out=' + _text(p)
        assert not led.exists(), 'a non-Bash tool_use must not write an arm ledger row'


def main():
    if not HOOK.is_file():
        print('FAIL all: ' + str(HOOK) + ' is not a file -- the PreToolUse hook this suite '
              'guards is gone')
        return 1
    tests = sorted((n, fn) for n, fn in globals().items() if n.startswith('test_') and callable(fn))
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print('PASS ' + name)
        except Exception as exc:  # a test that dies is a fail, printed whole -- never truncated
            failed += 1
            print('FAIL ' + name + ': ' + type(exc).__name__ + ': ' + exc)
    print(str(len(tests) - failed) + '/' + str(len(tests)) + ' passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
