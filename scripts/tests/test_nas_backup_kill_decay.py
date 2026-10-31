#!/usr/bin/env python3
'''t1254: a backup run that is KILLED must leave a row naming the signal.

RCA workspace/rca/dd33ac8f525f.md: the guarantee "the job died silently is no longer a possible
outcome" (scripts/nas_backup.sh:32-33) rests on one bash EXIT trap. Section 3 of that RCA refuted
it: an EXIT trap does not run on SIGKILL (probe: exit=137, no `died` row), and SIGKILL is the
"killed" case the trap's own detail string names. The 2026-09-01 half-backup is that class -- a
run that stopped and left a directory that looked current for ten days.

No existing suite catches it. scripts/tests/test_backup_report.py (the file the task names) is
t704's survey of backup_corpora.survey_bundles: it imports backup_corpora (:22), never runs
nas_backup.sh, and has no kill/-9/died/trap fixture. The harness that DOES run the real script in
a sandbox is scripts/tests/test_backup_records_head.py; its shape (NAS_BACKUP_REPO /
NAS_BACKUP_DEST env overrides + a stub backup_corpora.py recording argv) is reused here verbatim.

Red on main for the missing change and for nothing else:
  test_the_writer_exists_and_is_sandboxable     -- red if scripts/nas_backup.sh is absent.
  test_a_SIGKILLed_copy_leaves_a_row_naming_the_signal -- nas_backup.sh:49 handles the copy's
      death with `|| row copy-failed "backup_corpora --to did not complete"`: a status with no
      signal in it, for the death mode the finding is about. Red: no `died` row naming 9/KILL.
  test_a_SIGTERMed_run_records_the_signal_not_just_that_it_died -- nas_backup.sh:33's detail is
      "the backup exited without reaching its own end -- killed, crashed, or the NAS went away
      mid-run": it cannot tell a timeout from a crash. Red: names no 15/TERM.
  test_the_silent_death_claim_names_the_signals_it_handles -- pins the prose claim to the
      mechanism, so the claim cannot outlive the traps (RCA section 5, rung 2).

No live state: every path is under tempfile.mkdtemp; the sandbox refuses to run any script that
does not read NAS_BACKUP_DEST, so a sandboxed run can never touch the real /mnt/nas (the guard
test_backup_records_head.py:59-62 uses). No model call, no ps, no fixed /tmp path, timeout= on
every subprocess call, and every process this file starts is killed and reaped in `finally`.
'''
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / 'scripts' / 'nas_backup.sh'
HEALTHY = 'verified'          # nas_backup.sh:17-19 -- the good set, enumerated
TRAPPABLE = ('HUP', 'INT', 'QUIT', 'TERM')

# Stands in for scripts/backup_corpora.py inside the sandbox repo. Records argv (so a test can
# prove the real copy call was reached) and, when NAS_BACKUP_TEST_HANG names a file, writes its
# OWN pid there and sleeps: that gives the fixture a real, long-lived "copy in progress" to
# kill, at the exact moment the nightly run is exposed.
STUB = '\n'.join([
    'import os, pathlib, sys, time',
    'log = os.environ.get("NAS_BACKUP_TEST_LOG")',
    'if log:',
    '    pathlib.Path(log).open("a").write(" ".join(sys.argv[1:]) + "\\n")',
    'if len(sys.argv) > 1 and sys.argv[1] == "--to":',
    '    d = pathlib.Path(sys.argv[2]) / "ai-workspace-corpora"',
    '    d.mkdir(parents=True, exist_ok=True)',
    '    (d / "corpusfile").write_text("payload\\n")',
    '    (d / "MANIFEST.sha256").write_text("deadbeef  corpusfile\\n")',
    'hang = os.environ.get("NAS_BACKUP_TEST_HANG")',
    'if hang and len(sys.argv) > 1 and sys.argv[1] == "--to":',
    '    pathlib.Path(hang).write_text(str(os.getpid()))',
    '    time.sleep(120)',
    'sys.exit(0)',
]) + '\n'


def git(repo, *args, timeout=60):
    return subprocess.run(['git', '-C', str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def sandbox(base):
    '''A fake repo with one commit (git bundle needs a ref) and the stub in scripts/.'''
    text = SCRIPT.read_text(encoding='utf-8')          # FileNotFoundError if the writer is gone
    assert 'NAS_BACKUP_DEST' in text, ('scripts/nas_backup.sh does not read NAS_BACKUP_DEST, so a '
                                       'sandboxed run would touch the real /mnt/nas -- live state. '
                                       'Refusing to run it.')
    repo, dest = base / 'repo', base / 'dest'
    (repo / 'scripts').mkdir(parents=True)
    (repo / 'workspace').mkdir(parents=True)
    dest.mkdir(parents=True)
    assert git(repo, 'init', '-q').returncode == 0
    (repo / 'note.txt').write_text('v1\n', encoding='utf-8')
    assert git(repo, 'add', 'note.txt').returncode == 0
    assert git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t',
               'commit', '-qm', 'c1').returncode == 0
    (repo / 'scripts' / 'backup_corpora.py').write_text(STUB, encoding='utf-8')
    return repo, dest


def env_for(repo, dest, base, hang):
    env = dict(os.environ)
    env['NAS_BACKUP_REPO'] = str(repo)
    env['NAS_BACKUP_DEST'] = str(dest)
    env['NAS_BACKUP_TEST_LOG'] = str(base / 'argv.log')
    env['NAS_BACKUP_TEST_HANG'] = str(hang)
    return env


def read_rows(ledger):
    if not ledger.exists():
        return []
    return [json.loads(l) for l in ledger.read_text(encoding='utf-8').splitlines() if l.strip()]


def wait_for_rows(ledger, count, budget=25.0):
    deadline = time.time() + budget
    rows = []
    while time.time() < deadline:
        rows = read_rows(ledger)
        if len(rows) >= count:
            return rows
        time.sleep(0.2)
    return rows


def start_copy(base, repo, dest, pid_file):
    '''Start the real script and block until the stub has reached the copy and recorded its pid.
    Returns (proc, victim_pid). Raises with the captured output if it never gets there.'''
    proc = subprocess.Popen(['bash', str(SCRIPT)], env=env_for(repo, dest, base, pid_file),
                            cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        if pid_file.exists():
            txt = pid_file.read_text(encoding='utf-8').strip()
            if txt.isdigit():
                return proc, int(txt)
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    out, err = proc.communicate(timeout=15)
    raise AssertionError('the stub never reached the copy (no pid in %s); exit=%s\n'
                         'stdout: %s\nstderr: %s' % (pid_file, proc.poll(), out, err))


def cleanup(proc, victim):
    if victim:
        try:
            os.kill(victim, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    if proc is not None and proc.poll() is None:
        proc.kill()
        try:
            proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                pass


def test_the_writer_exists_and_is_sandboxable():
    assert SCRIPT.is_file(), 'no backup writer at %s -- nothing to test' % SCRIPT
    text = SCRIPT.read_text(encoding='utf-8')
    assert 'NAS_BACKUP_REPO' in text and 'NAS_BACKUP_DEST' in text, (
        'scripts/nas_backup.sh no longer honours NAS_BACKUP_REPO/NAS_BACKUP_DEST, so every test '
        'in this file would run against the live /mnt/nas -- refusing to proceed')


def test_a_SIGKILLed_copy_leaves_a_row_naming_the_signal():
    base = Path(tempfile.mkdtemp(prefix='t1254_kill_'))
    pid_file = base / 'copy.pid'
    proc, victim = None, None
    try:
        repo, dest = sandbox(base)
        ledger = repo / 'workspace' / 'backup-runs.jsonl'
        before = len(read_rows(ledger))
        proc, victim = start_copy(base, repo, dest, pid_file)
        argv = (base / 'argv.log').read_text(encoding='utf-8')
        assert '--to' in argv, ('the stub never received the --to copy call, so this fixture '
                                'would not be exercising the real copy: %r' % argv)
        assert str(dest) in argv, ('the copy is not aimed at the sandbox destination: %r' % argv)

        os.kill(victim, signal.SIGKILL)          # the death the trap string names, uncatchable
        rc = proc.wait(timeout=90)
        assert rc != 0, 'a run whose copy process was SIGKILLed must not report success'

        rows = wait_for_rows(ledger, before + 1)
        added = rows[before:]
        assert added, ('a SIGKILLed copy left NO ledger row: the guarantee at nas_backup.sh:32 '
                       '("a crash or a kill still leaves a row") is false for SIGKILL -- bash '
                       'EXIT traps do not run on 9, and the `||` branch at line 49 is never '
                       'reached because the shell is dead')
        row = added[-1]
        assert row.get('job') == 'nas-backup', row
        assert row.get('status') == 'died', (
            'a killed copy recorded as %r; the death of a copy process to a signal is a DIED '
            'run, not the generic copy-failed of a copy that ran and failed: %r'
            % (row.get('status'), row))
        detail = str(row.get('detail', ''))
        assert '9' in detail, 'the detail names no signal number: %r' % row
        assert 'KILL' in detail.upper(), 'the detail does not say which signal: %r' % row
        assert not any(r.get('status') == HEALTHY for r in rows), (
            'a run whose copy was SIGKILLed must never leave a verified row: %r' % rows)
        at = str(row.get('at', ''))
        assert 'T' in at and at.endswith('Z'), 'the row does not say when: %r' % row
    finally:
        cleanup(proc, victim)
        shutil.rmtree(base, ignore_errors=True)


def test_a_SIGTERMed_run_records_the_signal_not_just_that_it_died():
    base = Path(tempfile.mkdtemp(prefix='t1254_term_'))
    pid_file = base / 'copy.pid'
    proc, victim = None, None
    try:
        repo, dest = sandbox(base)
        ledger = repo / 'workspace' / 'backup-runs.jsonl'
        before = len(read_rows(ledger))
        proc, victim = start_copy(base, repo, dest, pid_file)
        os.kill(proc.pid, signal.SIGTERM)        # what `systemctl --user stop` sends
        time.sleep(1.0)                          # bash runs traps at the next command boundary
        os.kill(victim, signal.SIGKILL)          # the 90s grace period's final kill
        rc = proc.wait(timeout=90)
        assert rc != 0, 'a run that was asked to stop must not report success'

        rows = wait_for_rows(ledger, before + 1)
        added = rows[before:]
        assert added, 'a SIGTERMed run left no ledger row: %r' % rows
        row = added[-1]
        assert row.get('status') == 'died', row
        detail = str(row.get('detail', ''))
        assert '15' in detail, (
            'the detail cannot tell a stop/timeout from a crash; nas_backup.sh:33 writes one '
            'generic string for every death: %r' % row)
        assert 'TERM' in detail.upper(), 'the detail does not say which signal: %r' % row
        assert not any(r.get('status') == HEALTHY for r in rows), (
            'a run that was stopped must never leave a verified row: %r' % rows)
    finally:
        cleanup(proc, victim)
        shutil.rmtree(base, ignore_errors=True)


def test_the_silent_death_claim_names_the_signals_it_handles():
    text = SCRIPT.read_text(encoding='utf-8')
    claims = [l.strip() for l in text.splitlines() if 'died silently' in l]
    assert claims, ('scripts/nas_backup.sh no longer makes the "died silently" claim -- this test '
                    'is stale; delete it in the same commit that removes the claim')
    traps = [l for l in text.splitlines() if l.strip().startswith('trap ')]
    caught = set()
    for line in traps:
        up = line.upper()
        for name in TRAPPABLE:
            if name in up:
                caught.add(name)
    missing = sorted(set(TRAPPABLE) - caught)
    assert not missing, (
        'the file claims a death never goes unrecorded (%r) but no trap handles %s; today only '
        'bash EXIT (:33) is trapped, so a run stopped by one of those signals is recorded only '
        'as the generic "died" with no cause, and a reader cannot tell a timeout from a crash'
        % (claims[0], ', '.join(missing)))


TESTS = (test_the_writer_exists_and_is_sandboxable,
         test_a_SIGKILLed_copy_leaves_a_row_naming_the_signal,
         test_a_SIGTERMed_run_records_the_signal_not_just_that_it_died,
         test_the_silent_death_claim_names_the_signals_it_handles)

if __name__ == '__main__':
    failures = 0
    for fn in TESTS:
        try:
            fn()
            print('ok   %s' % fn.__name__)
        except Exception as e:  # noqa: BLE001 -- report the cause, never swallow it
            failures += 1
            print('FAIL %s: %s' % (fn.__name__, e))
    if failures:
        print('%d test(s) failed' % failures)
        sys.exit(1)
    print('all tests passed')
    sys.exit(0)
