#!/usr/bin/env python3
"""The merge lock: one merge into main at a time, an flock outside every worktree (2026-10-27).

Jas: "FIRST, before anything else: the merge lock (one merge into main at a time, under flock)."
Four lanes fast-forward four branches into one `main`. Two at once is not a conflict git reports;
it is a read-decide-write race over refs/heads/main. Nothing serialised it.

The lock is a file, so the test proves the PROPERTY the file exists for: with the lock held, a
second merge does not start; when it is released, the next one does; and a holder that dies does
not leave the repository locked. Each case runs the shipped command in a subprocess against a
throwaway LANES_HOME, so the live lock (~/.cache/hivemind-tmp/lanes/merge.lock) is never touched --
this suite runs while four lanes are working.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "merge_lock.py"
PASSED = FAILED = 0


def report(name, ok, detail=""):
    global PASSED, FAILED
    PASSED += bool(ok)
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        {detail}"))


def run(home, *args, timeout=120, extra_env=None):
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          env=dict(os.environ, LANES_HOME=str(home), **(extra_env or {})),
                          timeout=timeout)


def holder_process(home, seconds, lane="L2", why="test hold"):
    """A process that holds the lock for `seconds`, then exits. Returns it, already holding."""
    code = (f"import sys,time;sys.path.insert(0,{str(REPO / 'scripts')!r});import merge_lock;"
            f"m=merge_lock.hold({lane!r},{why!r});m.__enter__();print('HELD',flush=True);"
            f"time.sleep({seconds})")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True,
                         env=dict(os.environ, LANES_HOME=str(home)))
    assert p.stdout.readline().strip() == "HELD", "the holder never took the lock"
    return p


def main():
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        p = run(home, "status")
        report("a lock nobody holds reads free", p.returncode == 0 and "free" in p.stdout,
               p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        h = holder_process(home, 30)
        try:
            p = run(home, "run", "--lane", "L4", "--timeout", "0", "--", "true")
            report("with the lock held, a second merge does not start", p.returncode == 3,
                   p.stdout + p.stderr)
            report("  and it names the lane that holds it, so the lane knows who to ask",
                   "L2" in p.stderr and "test hold" in p.stderr, p.stderr)
            st = run(home, "status")
            report("  status names the holder while it is held",
                   "L2" in st.stdout and "held by" in st.stdout, st.stdout)
        finally:
            h.kill()
            h.wait()

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        h = holder_process(home, 3)
        try:
            # A waiter, not a refusal: the default behaviour is to wait for the lock, because a
            # lane's merge should queue, not fail.
            t0 = time.monotonic()
            p = run(home, "run", "--lane", "L3", "--timeout", "20", "--", "true")
            waited = time.monotonic() - t0
            ok = p.returncode == 0 and 0.5 < waited < 20
            report("a merge that is told to wait, waits, then runs when the lock is released",
                   ok, f"rc={p.returncode} waited={waited:.2f}s {p.stdout}{p.stderr}")
        finally:
            h.kill()
            h.wait()

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        # A HOLDER THAT DIES MUST NOT LOCK THE REPOSITORY. This is the failure the file alone
        # cannot avoid: the JSON is still there after a SIGKILL, so `status` has to ask the kernel
        # rather than read the file.
        h = holder_process(home, 30)
        h.kill()
        h.wait()
        p = run(home, "run", "--lane", "L1", "--timeout", "2", "--", "true")
        report("a killed holder leaves the lock free (the flock dies with the process)",
               p.returncode == 0, p.stdout + p.stderr)
        st = run(home, "status")
        report("  and status does not report the dead holder", "free" in st.stdout, st.stdout)

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        p = run(home, "run", "--lane", "L2", "--why", "ff-only", "--", "sh", "-c", "exit 7")
        report("the command's own exit code is the exit code (a failed merge still fails)",
               p.returncode == 7, f"rc={p.returncode} {p.stdout}{p.stderr}")
        p = run(home, "run", "--lane", "L2", "--timeout", "0", "--", "true")
        report("  and the lock is released even when the merge inside it fails",
               p.returncode == 0, p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        home.mkdir(parents=True)
        (home / "merge.lock").write_text("")
        p = run(home, "status")
        report("an empty lock file is free, not an error", p.returncode == 0 and "free" in p.stdout,
               p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        # A merge that hangs must not hold the lock forever: the child is killed, the lock is
        # released, and the exit code says what happened.
        p = subprocess.run([sys.executable, str(SCRIPT), "run", "--lane", "L2", "--", "sleep", "30"],
                           capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, LANES_HOME=str(home), MERGE_LOCK_CMD_TIMEOUT="1"))
        report("a merge that hangs is killed at CMD_TIMEOUT, not left holding the lock",
               p.returncode == 4 and "KILLED" in p.stderr, f"rc={p.returncode} {p.stderr}")
        p = run(home, "run", "--lane", "L1", "--timeout", "0", "--", "true")
        report("  and the lock is free afterwards", p.returncode == 0, p.stdout + p.stderr)

    # THE GATE IS ASKED BEFORE GIT RUNS (L3's 13:29Z feedback, 2026-10-27). reference-transaction
    # fires after the merge is already written into the index and the worktree, so a refusal there
    # leaves the live checkout half-merged. --preflight asks first and runs nothing on a refusal.
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        marker = Path(tmp) / "the-wrapped-command-ran"
        gate = Path(tmp) / "gate-refuse.py"
        gate.write_text("import sys\nprint('  REFUSED: the stub gate says no', file=sys.stderr)\n"
                        "sys.exit(1)\n", encoding="utf-8")
        fakegit = Path(tmp) / "fakegit.py"
        fakegit.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
        p = run(home, "run", "--lane", "L2", "--preflight", "l2/topic", "--",
                sys.executable, str(fakegit), "merge", "--no-ff", "l2/topic",
                extra_env={"MERGE_LOCK_ROLE_GATE": str(gate)})
        report("a refused preflight runs NOTHING and says so before git starts",
               p.returncode == 5 and not marker.exists() and "NOTHING WAS RUN" in p.stderr,
               f"rc={p.returncode} marker={marker.exists()} {p.stderr}")

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        marker = Path(tmp) / "ran"
        gate = Path(tmp) / "gate-allow.py"
        gate.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        fakegit = Path(tmp) / "fakegit.py"
        fakegit.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
        p = run(home, "run", "--lane", "L2", "--preflight", "l2/topic", "--",
                sys.executable, str(fakegit), "merge", "--no-ff", "l2/topic",
                extra_env={"MERGE_LOCK_ROLE_GATE": str(gate)})
        report("  an allowed preflight runs the command",
               p.returncode == 0 and marker.exists(), p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        marker = Path(tmp) / "ran"
        gate = Path(tmp) / "gate-allow.py"
        gate.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        fakegit = Path(tmp) / "fakegit.py"
        fakegit.write_text(f"open({str(marker)!r}, 'w').write('ran')\n", encoding="utf-8")
        p = run(home, "run", "--lane", "L2", "--preflight", "l2/topic", "--",
                sys.executable, str(fakegit), "merge", "--no-ff", "l3/OTHER",
                extra_env={"MERGE_LOCK_ROLE_GATE": str(gate)})
        report("  --preflight refuses to guard a command that merges a DIFFERENT branch",
               p.returncode != 0 and not marker.exists() and "does not merge" in p.stderr,
               p.stdout + p.stderr)

    # OPTION B (Jas, 2026-10-28): the lock asks the gate for EVERY merge, so a lane cannot skip it by
    # forgetting the flag. Measured cost 0.07-0.17 s, and it runs before the lock is taken.
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        marker = Path(tmp) / "ran"
        gate = Path(tmp) / "gate-refuse.py"
        gate.write_text("import sys\nprint('  REFUSED: stub', file=sys.stderr)\nsys.exit(1)\n")
        fakegit = Path(tmp) / "fakegit.py"
        fakegit.write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
        p = run(home, "run", "--lane", "L2", "--", sys.executable, str(fakegit),
                "merge", "--no-ff", "l2/topic",
                extra_env={"MERGE_LOCK_ROLE_GATE": str(gate)})
        report("a plain `git merge` is preflighted WITHOUT being asked to be",
               p.returncode == 5 and not marker.exists() and "NOTHING WAS RUN" in p.stderr,
               f"rc={p.returncode} marker={marker.exists()} {p.stderr}")

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        marker = Path(tmp) / "ran"
        gate = Path(tmp) / "gate-allow.py"
        gate.write_text("import sys\nsys.exit(0)\n")
        fakegit = Path(tmp) / "fakegit.py"
        fakegit.write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
        p = run(home, "run", "--lane", "L2", "--", sys.executable, str(fakegit),
                "merge", "--no-ff", "-F", str(Path(tmp) / "m.txt"), "l2/topic",
                extra_env={"MERGE_LOCK_ROLE_GATE": str(gate)})
        report("  an allowed merge runs, with the -F message still parsed out of the command",
               p.returncode == 0 and marker.exists(), p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "lanes"
        marker = Path(tmp) / "ran"
        fakegit = Path(tmp) / "fakegit.py"
        fakegit.write_text(f"open({str(marker)!r}, 'w').write('ran')\n")
        p = run(home, "run", "--lane", "L2", "--", sys.executable, str(fakegit), "status")
        report("  a command that is not a merge is not preflighted", p.returncode == 0 and marker.exists(),
               p.stdout + p.stderr)

    sys.path.insert(0, str(REPO / "scripts"))
    import merge_lock as ML
    report("merge_target reads the branch and the -F file",
           ML.merge_target(["git", "merge", "--no-ff", "-F", "m.txt", "l2/x"]) == ("l2/x", "m.txt"),
           str(ML.merge_target(["git", "merge", "--no-ff", "-F", "m.txt", "l2/x"])))
    report("  and refuses to guess a branch from a message or a flag",
           ML.merge_target(["git", "merge", "--abort"]) == ("", ""),
           str(ML.merge_target(["git", "merge", "--abort"])))

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
