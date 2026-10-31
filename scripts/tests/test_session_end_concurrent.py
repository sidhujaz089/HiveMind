#!/usr/bin/env python3
"""Two session ends on one box must not eat each other's work (2026-10-30).

Jas: "I want multiple session ends to happen at the same time with no issues." The box has ONE git index, so the
guarantee cannot be "they never touch the same file". It can be, and must be:

  * a session stages EXACTLY the paths it found, never `git add -A`, so it cannot sweep another session's work
    into its own commit;
  * a REFUSED commit leaves the index as it found it -- unstaged. On 2026-10-30 a session-end commit was killed
    mid-flight and left `.git/index.lock` plus a fully staged tree, and every merge that touched those paths then
    refused for hours;
  * a held index is WAITED FOR, not crashed on, and the wait is bounded and SAID.

Nothing here runs against the live checkout: every case is a throwaway repository.
RED ON MAIN: session_end has no commit_paths at all --
  BAD  a refused commit leaves the index unstaged  (AttributeError)
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import session_end as se          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


def git(repo, *a):
    return subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True).stdout.strip()


def repo_with(tmp, name, refuse_commit=True):
    r = Path(tmp) / name
    r.mkdir(parents=True)
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t")
    git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("a\n")
    (r / "b.txt").write_text("b\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "base")
    hook = r / ".git" / "hooks" / "pre-commit"
    if refuse_commit:
        hook.write_text("#!/bin/sh\necho '  COMMIT BLOCKED -- a hook said no' >&2\nexit 1\n")
        hook.chmod(0o755)
    return r


with tempfile.TemporaryDirectory() as td:
    # --- a refused commit must not leave the index staged -----------------------------------------
    r = repo_with(td, "refused")
    (r / "a.txt").write_text("a changed\n")
    rc, out = se.commit_paths(["a.txt"], "fix(workspace): x", "dsh-harness/test", root=r, wait=5)
    check("a refused commit reports failure", rc != 0, str(rc))
    check("a refused commit leaves the index unstaged (the lock-and-staged trap of 2026-10-30)",
          git(r, "diff", "--cached", "--name-only") == "", git(r, "diff", "--cached", "--name-only"))
    check("  and the work is still on disk, not lost", (r / "a.txt").read_text() == "a changed\n")

    # --- a session commits only what it staged ----------------------------------------------------
    r = repo_with(td, "two-sessions", refuse_commit=False)
    (r / "a.txt").write_text("a changed\n")
    (r / "b.txt").write_text("b changed\n")          # another session's work
    rc, out = se.commit_paths(["a.txt"], "fix(workspace): only mine", "dsh-harness/test", root=r, wait=5)
    check("the commit succeeds", rc == 0, out[-140:])
    check("only the staged path is in the commit",
          git(r, "show", "--name-only", "--format=", "HEAD").split() == ["a.txt"],
          git(r, "show", "--name-only", "--format=", "HEAD").split())
    check("another session's file is left alone", git(r, "status", "--porcelain").strip().endswith("b.txt"))

    # --- a second session can commit the rest afterwards -------------------------------------------
    rc2, out2 = se.commit_paths(["b.txt"], "fix(workspace): the other one", "claude-code/test", root=r, wait=5)
    check("a second session commits its own path afterwards", rc2 == 0, out2[-140:])
    check("  and the first session's commit is untouched",
          git(r, "show", "--name-only", "--format=", "HEAD^").split() == ["a.txt"])

    # --- a held index is waited for, and refused rather than crashed -------------------------------
    r = repo_with(td, "lock", refuse_commit=False)
    (r / "a.txt").write_text("a changed again\n")
    lock = r / ".git" / "index.lock"
    lock.write_text("")
    rc3, out3 = se.commit_paths(["a.txt"], "fix(workspace): x", "dsh-harness/test", root=r, wait=1)
    check("a held index does not crash the session end", rc3 != 0 and "index" in out3.lower(), out3[:140])
    check("  and it says nothing was staged", "nothing was staged" in out3.lower(), out3[:140])
    check("  and the held lock is left in place (it belongs to a live process)",
          lock.exists(), "removing another session's lock is how work is lost")
    lock.unlink()
    rc4, out4 = se.commit_paths(["a.txt"], "fix(workspace): x", "dsh-harness/test", root=r, wait=5)
    check("once the lock clears the commit goes through", rc4 == 0, out4[-140:])

# --- D: the full check is paid once per world state ---------------------------------------------------
class _Proc:
    def __init__(self, rc):
        self.returncode = rc


with tempfile.TemporaryDirectory() as tdc:
    os.environ["TEST_VERDICT_CACHE"] = tdc
    r = repo_with(tdc, "full-check")
    (r / "scripts").mkdir(exist_ok=True)
    calls = []

    def fake_run(cmd):
        calls.append(cmd)
        return _Proc(1)

    services = {"v": "service-state-A"}
    old_root = se.REPO_ROOT
    se.REPO_ROOT = r
    try:
        rc1, reused1 = se.full_check(run=fake_run, services=lambda: services["v"]); n1 = len(calls)
        rc2, reused2 = se.full_check(run=fake_run, services=lambda: services["v"]); n2 = len(calls)
        rc3, reused3 = se.full_check(run=fake_run, services=lambda: services["v"], force=True); n3 = len(calls)
        services["v"] = "service-state-B"           # a restart, no file changed
        rc4, reused4 = se.full_check(run=fake_run, services=lambda: services["v"]); n4 = len(calls)
    finally:
        se.REPO_ROOT = old_root
    check("the first full check runs", n1 == 1 and reused1 is False, f"{n1} call(s)")
    check("the same world does NOT pay for it twice", reused2 is True and n2 == 1, f"{n2} call(s)")
    check("  and it reports the answer the first run gave", rc1 == rc2 == 1, f"{rc1} vs {rc2}")
    check("--force-check pays again on purpose", reused3 is False and n3 == 2, f"{n3} call(s)")
    check("a SERVICE change invalidates it, because no file records a restart",
          reused4 is False and n4 == 3, f"{n4} call(s)")


# --- E: a close-out does not re-do a timer's job ------------------------------------------------
# A ROUTINE WITH NO TIMER STILL RUNS -- including one that does not exist yet, which is why the entry is injected
# rather than borrowed from the real table: after 2026-10-30 every real routine has a home.
se.SLOW_ROUTINES["_probe"] = {"cmd": "true", "timer": None, "stale_after_h": None, "what": "a probe"}
check("a routine with no timer still runs", se.routine_decision("_probe")[0] is True)
se.SLOW_ROUTINES.pop("_probe", None)
run, line = se.routine_decision("corpus", age_hours=0.5)
check("a routine whose timer just ran is skipped", run is False, line)
check("  and the skip names the timer", "corpus-index.timer" in line, line)
run, line = se.routine_decision("corpus", age_hours=99.0)
check("a routine whose timer has gone quiet RUNS", run is True, line)
check("  and it says the timer is behind", "behind" in line.lower() or "stale" in line.lower(), line)
run, line = se.routine_decision("corpus", asked=True, age_hours=0.5)
check("asking for it overrides a fresh timer", run is True, line)
run, line = se.routine_decision("corpus", age_hours=None)
check("no timer record means it runs, not a silent skip", run is True, line)
run, line = se.routine_decision("apps", fast=True)
# --- the app pipeline and the digests now have a home -------------------------------------------
check("the app pipeline has a timer home", se.SLOW_ROUTINES["apps"]["timer"] == "slow-checks.timer",
      str(se.SLOW_ROUTINES["apps"]))
check("  and so do the digests", se.SLOW_ROUTINES["digests"]["timer"] == "slow-checks.timer",
      str(se.SLOW_ROUTINES["digests"]))
_run, _line = se.routine_decision("apps", age_hours=3.0)
check("a fresh record means the close-out leaves the app pipeline alone",
      _run is False and "slow-checks.timer" in _line, _line)
_run, _line = se.routine_decision("digests", age_hours=40.0)
check("a record that is behind means the close-out runs it", _run is True and "BEHIND" in _line, _line)

check("--fast skips it and says how to run it", run is False and "slow_checks.py" in line, line)


print("test_session_end_concurrent:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
