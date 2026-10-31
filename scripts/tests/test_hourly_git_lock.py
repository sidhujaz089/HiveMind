#!/usr/bin/env python3
"""t1544: the hourly check must report a stale .git/index.lock, and clear one no git process owns.

A lock left at 2026-10-21 02:00:58 with no git process alive blocked every commit on the box --
merges, freezes, sessions -- for ~2.5 h, and no check in scripts/ looked at the file. These tests
build a repo in a temp dir, set the lock's age with os.utime, hand in the live-git process list and
the clock, and assert the finding, the deletion that actually happened, and that a clear which
FAILED is recorded as a failure rather than swallowed.

No git, no ps, no model, no server, no ledger, no task. Every write is inside a temp dir the context
manager removes. The clock is injected, so each test's age is exact -- never a race with the clock.
"""
import os                      # os.utime: age a lock file the way git leaves one behind
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import hourly_checks as H

NAME = "stale .git/index.lock"
AGE = 3600


def _sym(name):
    """The symbol under test, named in the failure so a red run says what is missing (t1544)."""
    got = getattr(H, name, None)
    assert got is not None, (f"hourly_checks.{name} does not exist -- the stale .git/index.lock check "
                             f"(t1544) is not implemented")
    return got


def _lock(tmp):
    (tmp / ".git").mkdir(parents=True, exist_ok=True)
    lock = tmp / ".git" / "index.lock"
    lock.write_text("a lock git leaves behind when it dies mid-index", encoding="utf-8")
    return lock


def _aged(tmp, seconds):
    """A lock `seconds` old, and the clock value that makes its age exactly that."""
    lock = _lock(tmp)
    created = lock.stat().st_mtime
    os.utime(lock, (created - seconds, created - seconds))
    return lock, created + seconds


def test_the_threshold_is_the_tasks_ten_minutes():
    """'older than ~10 min' is one named constant, so the check and its reader quote the same number."""
    assert getattr(H, "LOCK_AGE_S", None) == 600, (
        "hourly_checks.LOCK_AGE_S is not 600 -- the task's ~10 minutes is the age a lock must pass "
        "before it is a finding (t1544)")


def test_no_lock_is_not_a_finding():
    """The common case: a repo with no lock yields no finding, no task, no process scan, no deletion."""
    find = _sym("git_lock_finding")
    with tempfile.TemporaryDirectory() as d:
        assert find(Path(d), now=time.time(), pids=[]) is None


def test_a_young_lock_is_quiet_and_the_threshold_is_the_reported_age():
    """A commit in flight owns the index for seconds: a young lock is not a finding, or every hour
    with a commit files a task. The same file aged to exactly the threshold IS one. The age the
    reader acts on is in the detail, and reporting does not delete."""
    fn = _sym("git_lock_finding")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        lock = _lock(tmp)
        created = lock.stat().st_mtime
        assert fn(tmp, now=created + 60, pids=[]) is None, "a 60s-old lock is a commit in flight, not a finding"
        found = fn(tmp, now=created + H.LOCK_AGE_S, pids=[])
        assert found is not None, "a lock aged to exactly LOCK_AGE_S must be the finding -- the comparison is off"
        assert str(int(H.LOCK_AGE_S)) in found["detail"], f"the detail must state the age: {found['detail']}"
        assert found["name"] == NAME, f"the finding's stable key: {found['name']!r}"
        assert lock.exists() is True, "reporting must not delete: only the clear does that, and only with no git alive"


def test_a_stale_lock_is_reported_cleared_and_recorded():
    """The task's own words -- report, and with no git process holding it, CLEAR. The deletion is
    observed on the filesystem, and the outcome text names the file it removed."""
    check = _sym("lock_check")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        lock, now = _aged(tmp, AGE)
        name, detail, outcome = check(tmp, lister=lambda: [], now=now, clear=True)
        assert name == NAME, f"the finding must be named {NAME}, not {name!r}"
        assert str(AGE) in detail, f"the detail must carry the age a reader acts on: {detail}"
        assert "no git process" in detail, f"the detail must say no git process owns it: {detail}"
        assert lock.exists() is False, "the lock is still on disk: the check reported the block and left it there"
        assert "removed" in outcome, f"the outcome must record the clear: {outcome!r}"
        assert str(lock) in outcome, f"the outcome must name the file it deleted, in full: {outcome!r}"
        assert (tmp / ".git").is_dir(), "clearing the lock must not remove the .git directory"


def test_a_lock_a_live_git_process_holds_is_reported_and_left_alone():
    """The protective negative: deleting a lock a running commit owns aborts that commit. A live git
    pid turns this into a report whose outcome says NOT cleared, naming the pid, and the file stays."""
    check = _sym("lock_check")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        lock, now = _aged(tmp, AGE)
        name, detail, outcome = check(tmp, lister=lambda: [4242], now=now, clear=True)
        assert name == NAME, f"a 3600s-old lock is a finding even when a git process holds it: {name!r}"
        assert str(AGE) in detail
        assert lock.exists() is True, "a lock a live git process owns must never be deleted -- that aborts the commit"
        assert "not cleared" in outcome.lower(), f"the outcome must say it did not clear, and why: {outcome!r}"
        assert "4242" in outcome, f"the outcome must name the live pid that owns the lock: {outcome!r}"


def test_a_clear_that_fails_is_recorded_and_not_swallowed():
    """The second half of the task: a clear that failed must never read as a clear that happened. The
    lock stays, and the outcome says NOT removed, with the OSError's cause and the age."""
    check = _sym("lock_check")

    def read_only(p):
        raise OSError(30, "Read-only file system")

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        lock, now = _aged(tmp, AGE)
        name, detail, outcome = check(tmp, lister=lambda: [], remover=read_only, now=now, clear=True)
        assert name == NAME
        assert lock.exists() is True, "a failed clear must leave the lock where it is"
        assert "NOT removed" in outcome, f"a failed clear must be recorded as a failure, not an ok: {outcome!r}"
        assert "Read-only file system" in outcome, f"the cause belongs in the record, not a bare code: {outcome!r}"
        assert str(AGE) in outcome, f"the age belongs in the record of the failure: {outcome!r}"


def test_the_process_table_is_read_from_proc_not_ps():
    """Only a process whose own name is `git` owns the index. A python process that mentions git, and
    a shell whose command line says 'git status', are not git: matching on the command line is L-105.
    `read` injects {pid: comm}, so no test reads a process table."""
    gpp = _sym("git_process_pids")
    assert gpp(read=lambda: {7: "git", 8: "python3", 9: "git status", 10: "git"}) == [7, 10]
    assert gpp(read=lambda: {}) == []


def test_the_hourly_run_reports_the_finding_records_the_clear_and_its_outcome():
    """main() cannot be run in a test: it imports scheduler_commands/session_review/taskstore (L181-183),
    runs git rev-parse (L185), files a task and appends to workspace/hourly-checks.jsonl (L216). So the
    wiring is pinned by its source: the finding reaches the dict that files the task, and the clear's
    outcome reaches the printed line and the ledger row. A build that defines the functions and never
    calls them is not a fix for a 2.5 h block nobody was alerted to."""
    import inspect
    src = inspect.getsource(H)
    for line in ('lock_name, lock_detail, lock_clear = lock_check(REPO, clear=not dry)',
                 'found[lock_name] = lock_detail',
                 '"lock": lock_clear',
                 'print("hourly-checks lock: " + lock_clear)'):
        assert line in src, f"main() never has {line!r} -- the check would never report, file a task, " \
                           f"or record the clear (t1544)"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                fails += 1
                print(f"FAIL {name}: {e}")
    print(f"{fails} failed")
    sys.exit(1 if fails else 0)
