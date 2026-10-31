#!/usr/bin/env python3
"""One merge into main at a time: an flock, taken by every lane before it touches refs/heads/main.

    python3 scripts/merge_lock.py status
    python3 scripts/merge_lock.py run [--lane L2] [--why "..."] [--timeout 600] -- git merge --ff-only dsh/x
    python3 scripts/merge_lock.py run --lane L2 --preflight dsh/x --message msg.txt -- git merge --no-ff dsh/x -F msg.txt

--preflight ASKS THE TWO-SIGNATURE GATE BEFORE GIT RUNS, and starts nothing if it refuses (exit 5).
That is the difference between a refusal that costs a typed message and one that costs a half-merged
working tree: reference-transaction fires on 'prepared', after git has already written the merge into
the index and the tree (L3 lost 14 staged files to exactly that on 2026-10-27). The branch you name
must be the branch the command merges -- guarding a different command than you are running is worse
than not guarding it, so a mismatch is refused too.

    import merge_lock
    with merge_lock.hold("L2", "merge dsh/l2-lane-owns"):
        subprocess.run(["git", "merge", "--ff-only", "dsh/l2-lane-owns"], check=True)

WHY (2026-10-27, Jas): "FIRST, before anything else: the merge lock (one merge into main at a time,
under flock)." Four instances now fast-forward their own branches into main. A fast-forward is a
ref update, and two of them at once is not a merge conflict -- it is worse: the second one's
`git merge --ff-only` evaluates `main` before the first has moved it, decides the history is
linear, and either loses the other lane's commit or leaves a working tree that does not match the
ref. Nothing in the repository serialised this. git's own index.lock covers the index for
milliseconds and says nothing about a merge's whole read-decide-write.

WHERE THE LOCK LIVES. Outside git, beside the lanes board (~/.cache/hivemind-tmp/lanes/merge.lock),
because each lane has its OWN worktree: a lock file inside the repository would be four files, one
per worktree, and would serialise nothing at all. LANES_HOME moves it, which is how the suite keeps
its hands off the live lock.

WHAT IT DOES NOT DO. It does not stop a merge made without it -- a person typing `git merge` runs
unlocked. That is the same honest limit role_gate.py states: these are conventions plus checks for
agents that are confused, not a kernel against a determined one. `status` at least makes the
window visible, and the holder's lane and reason are written INSIDE the lock file while it is held.

TWO BOUNDS, FOR TWO DIFFERENT STALLS. --timeout bounds the WAIT for the lock (600 s); CMD_TIMEOUT
bounds the merge itself (3600 s, env MERGE_LOCK_CMD_TIMEOUT), because a merge that hangs holds the
lock against all four lanes -- the failure the lock exists to prevent, arriving through the lock.

THE WAIT IS NOT A FAILURE MODE. The default 600 s is deliberately longer than a
fast-forward of this repository takes (seconds); a lane that waits 10 minutes for the lock has
found a merge that is stuck, and the message names who to ask.
"""
import contextlib
import errno
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("LANES_HOME") or Path.home() / ".cache/hivemind-tmp/lanes")
LOCK = HOME / "merge.lock"
POLL = 0.2
DEFAULT_TIMEOUT = 600.0
# A merge of this repository takes seconds. An hour means something is stuck, and a stuck merge
# holds the lock against every other lane -- the failure the lock exists to prevent, arriving
# through the lock itself. The child is killed and the lock released; the message says the index
# may need `git merge --abort`. Overridable so the suite can prove it in a second rather than an hour.
CMD_TIMEOUT = float(os.environ.get("MERGE_LOCK_CMD_TIMEOUT") or 3600)
# THE GATE THE PREFLIGHT ASKS. Overridable so a test can prove the WIRING (nothing runs on a
# refusal) without building a repository that the real gate would refuse for its own reasons --
# test_two_signature.py already proves what the gate decides.
GATE_TIMEOUT = float(os.environ.get("MERGE_LOCK_GATE_TIMEOUT") or 300)
ROLE_GATE = Path(os.environ.get("MERGE_LOCK_ROLE_GATE")
                 or (Path(__file__).resolve().parent / "role_gate.py"))


def now():
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


class LockTimeout(RuntimeError):
    """The lock was not free within the timeout. Carries the holder when one is readable."""


def _read_holder(fh):
    try:
        fh.seek(0)
        raw = fh.read().strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw}


def holder():
    """Who holds the lock right now, or None. Read-only: it never creates or locks anything."""
    if not LOCK.exists():
        return None
    try:
        with LOCK.open("r", encoding="utf-8") as fh:
            data = _read_holder(fh)
    except OSError:
        return None
    if not data:
        return None
    # A holder that died leaves its JSON behind but not its flock. Ask the kernel.
    try:
        with LOCK.open("r", encoding="utf-8") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return data
            fcntl.flock(fh, fcntl.LOCK_UN)
        return None
    except OSError:
        return data


@contextlib.contextmanager
def hold(lane="", why="", timeout=DEFAULT_TIMEOUT):
    """Hold the merge lock for the duration of the block. Waits up to `timeout` seconds."""
    HOME.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a+", encoding="utf-8") as fh:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as e:
                if e.errno not in (errno.EACCES, errno.EAGAIN):
                    raise
                if time.monotonic() >= deadline:
                    raise LockTimeout(holder())
                time.sleep(POLL)
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps({"lane": lane, "why": why, "pid": os.getpid(), "since": now()}))
            fh.flush()
            yield holder() or {}
        finally:
            try:
                fh.seek(0)
                fh.truncate()
                fh.flush()
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def _describe(h):
    if not h:
        return "free"
    return (f"held by {h.get('lane') or '(unstated lane)'} (pid {h.get('pid', '?')}) since "
            f"{h.get('since', '?')}" + (f" -- {h['why']}" if h.get("why") else ""))


def preflight_refusal(branch, msg, cmd):
    """Why the two-signature gate would refuse this merge, asked BEFORE git runs. None if it allows.

    reference-transaction fires on 'prepared', which is after git has written the merge into the
    index and the working tree -- a refusal there leaves the checkout half-merged (L3 lost 14 staged
    files to exactly that on 2026-10-27). Git runs no earlier hook for a merge, so the only place to
    make a refusal a no-op is before the command starts.
    """
    if "merge" not in cmd or branch not in cmd:
        return (f"  MERGE NOT STARTED -- --preflight names {branch!r}, but this command does not "
                f"merge it:\n    {' '.join(cmd)}\n  Guarding a different command than the one you "
                f"are running is worse than not guarding it.\n")
    args = [sys.executable, str(ROLE_GATE), "preflight", branch]
    if msg:
        args.append(msg)
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=GATE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return (f"  MERGE NOT STARTED -- the two-signature gate did not answer within "
                f"{GATE_TIMEOUT:.0f}s, so this merge was not attempted. Ask it yourself:\n"
                f"    python3 scripts/role_gate.py preflight {branch}\n")
    if r.returncode == 0:
        return None
    return ("  MERGE NOT STARTED -- the two-signature gate refuses this merge, and NOTHING WAS RUN OR "
            "STAGED.\n" + (r.stderr or r.stdout))


def merge_target(cmd):
    """(branch, message-file) if `cmd` is a `git merge` of a branch, else ("", "").

    OPTION B, Jas 2026-10-28: the gate must be asked by the LOCK, not remembered by the caller. Twice on
    2026-10-28 a lane ran `preflight` and the merge in one command line, so a refusal did not stop the
    merge, and the live checkout sat half-merged while production ran. A flag you must remember is a flag
    that gets skipped exactly when someone is hurried.

    Measured cost of doing it always: 0.17 s when the branch brings guarded files, 0.07 s when it does
    not, against a merge that takes 1.3-12.5 s -- and it happens BEFORE the lock is taken, so it cannot
    lengthen the queue anyone waits in.

    Only `-F <file>` is read, because that is how this repository writes merge messages (a refused
    commit loses an inline `-m`). A `-m` message is not reconstructed here: preflight without the message
    refuses a guarded merge, which is the safe direction to be wrong in.
    """
    cmd = list(cmd or [])
    if "merge" not in cmd:
        return "", ""
    tail = cmd[cmd.index("merge") + 1:]
    branch, msg = "", ""
    i = 0
    while i < len(tail):
        a = tail[i]
        if a in ("-F", "--file") and i + 1 < len(tail):
            msg, i = tail[i + 1], i + 2
            continue
        if a in ("-m", "--message") and i + 1 < len(tail):
            i += 2
            continue
        if not a.startswith("-") and not branch:
            branch = a
        i += 1
    return branch, msg


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "status":
        h = holder()
        print(f"  merge lock: {_describe(h)}")
        print(f"  file: {LOCK}")
        return 0
    if cmd == "run":
        lane = why = branch = msg = ""
        timeout = DEFAULT_TIMEOUT
        rest = argv[1:]
        while rest and rest[0].startswith("--"):
            if rest[0] == "--":
                rest = rest[1:]
                break
            if rest[0] == "--lane" and len(rest) > 1:
                lane, rest = rest[1], rest[2:]
            elif rest[0] == "--why" and len(rest) > 1:
                why, rest = rest[1], rest[2:]
            elif rest[0] == "--timeout" and len(rest) > 1:
                timeout, rest = float(rest[1]), rest[2:]
            elif rest[0] == "--preflight" and len(rest) > 1:
                branch, rest = rest[1], rest[2:]
            elif rest[0] == "--message" and len(rest) > 1:
                msg, rest = rest[1], rest[2:]
            else:
                print(f"  unknown option {rest[0]!r}", file=sys.stderr)
                return 2
        if not rest:
            print("  nothing to run: merge_lock.py run [options] -- <command ...>", file=sys.stderr)
            return 2
        # OPTION B: ask the gate for EVERY merge, without being told to. An explicit --preflight still
        # works and still has to name the branch the command merges; otherwise the branch is read off
        # the command itself.
        auto_branch, auto_msg = merge_target(rest)
        if not branch and auto_branch:
            branch, msg = auto_branch, auto_msg
        if branch:
            # Ask the gate FIRST: a refusal here costs nothing, and the same refusal from the
            # ref-update hook costs a half-merged working tree.
            refusal = preflight_refusal(branch, msg, rest)
            if refusal:
                print(refusal, file=sys.stderr)
                return 5
        try:
            with hold(lane, why, timeout):
                t0 = time.monotonic()
                try:
                    rc = subprocess.run(rest, timeout=CMD_TIMEOUT).returncode
                except subprocess.TimeoutExpired:
                    print(f"  MERGE KILLED -- {rest[0]} did not finish within {CMD_TIMEOUT:.0f}s and "
                          f"was stopped.\n  The lock is released. The index may be mid-merge: check "
                          f"`git status`\n  and `git merge --abort` before trying again.\n",
                          file=sys.stderr)
                    return 4
                dt = time.monotonic() - t0
                print(f"  [merge lock: {rest[0]} finished in {dt:.1f}s, exit {rc}]", file=sys.stderr)
                return rc
        except LockTimeout as e:
            print(f"  MERGE NOT STARTED -- the merge lock was not free within {timeout:.0f}s.\n"
                  f"  {_describe(e.args[0] if e.args else None)}\n"
                  f"  Ask that lane, or wait: python3 scripts/merge_lock.py status\n", file=sys.stderr)
            return 3
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
