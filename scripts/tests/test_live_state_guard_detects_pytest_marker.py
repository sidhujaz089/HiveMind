#!/usr/bin/env python3
"""A process that says it is a test cannot mint a task into the live task store.

THE DEFECT (workspace/rca/check-14c2b455858d.md): the task-write guard's own self-test minted a
real task into the live tasks directory as its instrument, leaving a task file with no QUEUE.md
row, and the hourly check 'QUEUE.md and the task files name the same set' went red. Every fix so
far repaired one suite; this suite pins the barrier so the next suite cannot do it.

RED ON MAIN FOR ONE REASON, stated: scripts/live_state_guard.py lines 35-39, in_test, honours a
test-shaped argv[0] and HIVEMIND_IN_TEST and NOT the runner marker PYTEST_CURRENT_TEST -- the name
does not appear in that file (verified 2026-10-28 at HEAD 6cf8b44be; the only hit in the tree is
inside .venv). The RCA names that marker in the barrier's definition, so a suite run under pytest
is not detected and is free to mint. test_pytest_marker_cannot_mint_the_live_store is the red one.

The other three tests PASS on main today, because taskstore.reserve_id already refuses from a
test-shaped identity (projects/app-repo/taskstore.py:46-56, 485-500) and honours HIVEMIND_IN_TEST.
They are the DECAY TEST: delete the guard at taskstore.py:486 and the mint exits 0, a task file
and a QUEUE.md row appear, and the refusal tests stop refusing.

NOTHING HERE TOUCHES THE REAL STORE. Every mint target is a tempfile dir; the child points the
store at tmp/repo/workspace/tasks and patches the un-repointable real_live_root at tmp/repo, so
the space IS the live store as far as reserve_id can tell -- and a removed barrier writes into the
temp tree, never into workspace/tasks. No model call, no server, no process outlives the run.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_REPO = ROOT / 'projects' / 'app-repo'
SCRIPTS = ROOT / 'scripts'
CHILD_TIMEOUT = 90

SCRIPT_NAMES = {
    'test_shape': 'test_probe_selftest.py',
    'pytest_marker': 'probe_runner.py',
    'env_marker': 'probe_runner.py',
    'plain': 'merge_gate.py',
}

CHILD = r'''
import os
import sys
from pathlib import Path

APP_REPO, SCRIPTS, MODE, ROOTFAKE = sys.argv[1], sys.argv[2], sys.argv[3], Path(sys.argv[4])
LIVE = ROOTFAKE / 'workspace' / 'tasks'
if MODE == 'pytest_marker':
    os.environ['PYTEST_CURRENT_TEST'] = 'test_probe_selftest.py::test_x (call)'
if MODE == 'env_marker':
    os.environ['HIVEMIND_IN_TEST'] = '1'
os.environ['HIVEMIND_TASKS_DIR'] = str(LIVE)
sys.path.insert(0, APP_REPO)
sys.path.insert(0, SCRIPTS)
import live_state_guard
import taskstore

# The un-repointable live root (taskstore.py:406) points at the fake checkout, so the store the
# suite mints into resolves to <fake>/workspace/tasks -- the equality reserve_id tests at
# taskstore.py:487 before it refuses. Without this the guard cannot fire in a fixture at all.
taskstore.real_live_root = lambda: ROOTFAKE
assert str(Path(taskstore.TASKS).resolve()) == str(LIVE.resolve()), 'store is not the temp dir'
print('TASKS=' + str(taskstore.TASKS))
print('in_test=' + str(live_state_guard.in_test()))
try:
    tid = taskstore.add('BARRIER PROBE -- delete me', area='workspace', source='oracle')
except RuntimeError as exc:
    print('REFUSED ' + type(exc).__name__ + ' ' + str(exc))
    raise SystemExit(3)
print('MINTED ' + str(tid))
raise SystemExit(0)
'''


def _child(mode, fake_root):
    '''Run the child from a file NAMED per its mode, so argv[0] carries the identity under test.'''
    script = fake_root / SCRIPT_NAMES[mode]
    script.write_text(CHILD, encoding='utf-8')
    env = dict(os.environ)
    env.pop('HIVEMIND_IN_TEST', None)
    env.pop('PYTEST_CURRENT_TEST', None)
    return subprocess.run([sys.executable, str(script), str(APP_REPO), str(SCRIPTS), mode,
                           str(fake_root)], capture_output=True, text=True,
                          timeout=CHILD_TIMEOUT, cwd=str(fake_root), env=env)


def _store(inside):
    '''A fake live checkout shaped the way real_live_root's child path builds one.'''
    fake_root = Path(inside) / 'repo'
    tasks = fake_root / 'workspace' / 'tasks'
    tasks.mkdir(parents=True)
    return fake_root, tasks


def _assert_store_is_the_temp_dir(out, tasks):
    lines = [ln for ln in out.splitlines() if ln.startswith('TASKS=')]
    assert lines, 'child printed no TASKS= line: ' + ' '.join(out.stderr.split())[:600]
    assert lines[0] == 'TASKS=' + str(tasks), (
        'the child must mint into the temp store, never the real workspace/tasks: ' + lines[0])


def _task_files(tasks):
    return sorted(p.name for p in tasks.glob('t*--*.md'))


def test_a_test_shaped_process_cannot_mint_and_writes_nothing():
    with tempfile.TemporaryDirectory() as td:
        fake_root, tasks = _store(td)
        out = _child('test_shape', fake_root)
        _assert_store_is_the_temp_dir(out.stdout, tasks)
        assert 'in_test=True' in out.stdout, (
            'a suite named test_*.py must be detected by in_test; got: ' + out.stdout)
        assert out.returncode == 3, (
            'a test-shaped process must be refused before writing; exit=' + str(out.returncode)
            + ' out=' + out.stdout + ' err=' + out.stderr)
        assert 'REFUSED' in out.stdout, 'no refusal raised: ' + out.stdout + out.stderr
        assert 'HIVEMIND_TASKS_DIR' in out.stdout, (
            'the refusal must name the fix a suite author can act on (RCA item 2); got: '
            + out.stdout)
        assert _task_files(tasks) == [], (
            'a refused mint must create no task file; found ' + str(_task_files(tasks)))
        assert not (tasks / 'QUEUE.md').exists(), (
            'a refused mint must create no queue row -- the defect was a file with no row')
        assert not (tasks / '.ids').exists(), (
            'a refused mint must not advance the id counter -- 494 ids were burned this way')


def test_pytest_marker_cannot_mint_the_live_store():
    '''RED ON MAIN: live_state_guard.in_test ignores PYTEST_CURRENT_TEST (live_state_guard.py:35-39).'''
    with tempfile.TemporaryDirectory() as td:
        fake_root, tasks = _store(td)
        out = _child('pytest_marker', fake_root)
        _assert_store_is_the_temp_dir(out.stdout, tasks)
        assert 'in_test=True' in out.stdout, (
            'a process carrying the runner marker PYTEST_CURRENT_TEST must count as a test; '
            'in_test said no -- this is the RCA gap; stdout=' + out.stdout + ' err=' + out.stderr)
        assert out.returncode == 3, (
            'a pytest-marked process must be refused before writing; exit=' + str(out.returncode)
            + ' in_test detected it? ' + ('yes' if 'in_test=True' in out.stdout else 'no')
            + ' out=' + out.stdout + ' err=' + out.stderr)
        assert 'REFUSED' in out.stdout, 'no refusal raised: ' + out.stdout + out.stderr
        assert 'HIVEMIND_TASKS_DIR' in out.stdout, (
            'the refusal must name HIVEMIND_TASKS_DIR; got: ' + out.stdout)
        assert _task_files(tasks) == [], (
            'a refused mint must create no task file; found ' + str(_task_files(tasks)))
        assert not (tasks / 'QUEUE.md').exists(), 'a refused mint must create no queue row'


def test_env_marker_cannot_mint_and_writes_nothing():
    with tempfile.TemporaryDirectory() as td:
        fake_root, tasks = _store(td)
        out = _child('env_marker', fake_root)
        _assert_store_is_the_temp_dir(out.stdout, tasks)
        assert 'in_test=True' in out.stdout, (
            'HIVEMIND_IN_TEST must still be honoured (the change adds a marker, it does not '
            'replace the existing ones); got: ' + out.stdout)
        assert out.returncode == 3, (
            'a runner-marked process must be refused; exit=' + str(out.returncode)
            + ' out=' + out.stdout + ' err=' + out.stderr)
        assert 'HIVEMIND_TASKS_DIR' in out.stdout, 'refusal must name the fix: ' + out.stdout
        assert _task_files(tasks) == [], 'refused mint wrote ' + str(_task_files(tasks))


def test_an_ordinary_process_still_mints_the_file_and_the_row():
    '''The barrier is an allowlist, not a ban: a non-test process must mint, file AND row.'''
    with tempfile.TemporaryDirectory() as td:
        fake_root, tasks = _store(td)
        out = _child('plain', fake_root)
        _assert_store_is_the_temp_dir(out.stdout, tasks)
        assert 'in_test=False' in out.stdout, (
            'merge_gate.py with no runner markers must NOT look like a test; got: ' + out.stdout)
        assert out.returncode == 0, (
            'an ordinary writer must be allowed; exit=' + str(out.returncode)
            + ' out=' + out.stdout + ' err=' + out.stderr)
        files = _task_files(tasks)
        assert len(files) == 1, 'an allowed mint must create exactly one task file: ' + str(files)
        assert 'probe' in files[0], 'the file is the probe task: ' + files[0]
        assert (tasks / 'QUEUE.md').is_file(), (
            'the allowed mint must write the queue row too -- file-without-a-row is the defect')
        tid = [ln.split('MINTED ')[1].strip() for ln in out.stdout.splitlines()
               if ln.startswith('MINTED ')]
        assert tid, 'child reported no id: ' + out.stdout
        assert tid[0] in (tasks / 'QUEUE.md').read_text(encoding='utf-8'), (
            'QUEUE.md must name the id that was minted (' + tid[0] + ')')


def _run_all():
    tests = sorted((n, f) for n, f in list(globals().items())
                   if n.startswith('test_') and callable(f))
    fails = 0
    for name, fn in tests:
        try:
            fn()
            print('PASS ' + name)
        except Exception as exc:
            fails += 1
            print('FAIL ' + name + ': ' + ' '.join(str(exc).split()))
    print('%d/%d passed' % (len(tests) - fails, len(tests)))
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(_run_all())
