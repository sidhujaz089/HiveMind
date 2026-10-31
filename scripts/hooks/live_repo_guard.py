#!/usr/bin/env python3
"""Refuse `git switch`, detaching HEAD and `git stash` in the LIVE checkout (Phase 0a, lane L1).

    python3 scripts/hooks/live_repo_guard.py ref-update      # stdin of reference-transaction "prepared"
    python3 scripts/hooks/live_repo_guard.py path-checkout   # from post-checkout when $3 = 0

WHY. ~/ai-workspace is not a scratch checkout: the services write under it and four lanes commit to
it. A `git switch` there moves every running service onto another branch's files; a stash takes
their uncommitted ledger rows away. Both have happened (memory: never-git-stash-live-repo; role_gate's
`git checkout -b dsh/...` note). Work belongs in worktrees under ~/.cache/hivemind-tmp.

WHAT IS REFUSED, and where (git 2.53):
  * `switch` / `checkout <branch|sha>` / `checkout -b`: UNDONE by post-checkout, which switches back to
    main and exits 1. Not refused in the ref transaction, because by then git has already rewritten
    the tree and index: a refusal there leaves main's HEAD over the other branch's files.
  * any other HEAD write off main (symbolic-ref, update-ref, ...): refused in the ref transaction.
  * refs/stash gaining an entry: refused in the ref transaction (stash writes its ref first).
Dropping a stash entry (new id all zeros) is allowed: that is cleanup.

WHAT IS ALLOWED, because the line does it every day (checked against a fixture in
scripts/tests/test_live_repo_guard.py): commits on main, `merge --no-ff` and `merge --ff-only` into
main, `merge --abort`, `branch -d`, `worktree add/remove`, and anything at all in a linked worktree
or any other repository. A commit on main never writes HEAD itself, only refs/heads/main.

THE LIVE CHECKOUT is the MAIN worktree (git-dir == common-dir) whose top level is ~/ai-workspace,
or $HIVEMIND_LIVE_REPO when set (tests). Clones and linked worktrees are never live.

PATH CHECKOUT (`git checkout -- <file>`, `git restore`) moves no ref, so no hook can refuse it before
it happens. post-checkout reports it afterwards, loudly, so it is at least seen. That is the honest limit.

ESCAPE HATCH for Jas: HIVEMIND_LIVE_GIT_OK=1 lets one command through and says so.
"""
import os
import subprocess
import sys
from pathlib import Path

ZERO = "0" * 40
MAIN = "ref:refs/heads/main"


def _git(*args):
    p = subprocess.run(["git", *args], capture_output=True, text=True, timeout=10)
    return p.stdout.strip() if p.returncode == 0 else ""


def is_live():
    gd, cd, top = (_git("rev-parse", "--absolute-git-dir"), _git("rev-parse", "--git-common-dir"),
                   _git("rev-parse", "--show-toplevel"))
    if not gd or not top:
        return False
    if os.path.realpath(gd) != os.path.realpath(os.path.join(top, cd) if not os.path.isabs(cd) else cd):
        return False                                    # a linked worktree
    live = os.environ.get("HIVEMIND_LIVE_REPO") or str(Path.home() / "ai-workspace")
    return os.path.realpath(top) == os.path.realpath(live)


def git_command(pid=None, depth=4):
    """argv of the nearest ancestor that is git itself (the hook runs under sh, under git)."""
    pid = pid or os.getppid()
    for _ in range(depth):
        try:
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            argv = [a.decode(errors="replace") for a in argv if a]
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return []
        if argv and os.path.basename(argv[0]) == "git":
            return argv
        pid = int(stat.rsplit(")", 1)[1].split()[1])      # field 4, ppid
    return []


# git's global options that take a VALUE as the next argv entry (L4, 2026-10-27T05:34Z: every script
# in the repo calls `git -C <repo> worktree add`, and the first draft read the -C path as the command).
VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env",
              "--exec-path", "--list-cmds", "--attr-source"}


def subcommand(argv):
    """`git -C x -c k=v worktree add ...` -> "worktree". Skips global options and their values."""
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in VALUE_OPTS:
            i += 2
        elif a.startswith("-"):
            i += 1
        else:
            return a
    return ""


def refusals(lines, sub=None):
    """The reasons to refuse, one per offending line of the transaction."""
    out = []
    for line in lines:
        parts = line.split()
        if len(parts) != 3:
            continue
        _old, new, ref = parts
        if ref == "HEAD" and new != ZERO and new != MAIN:
            if sub is None:
                sub = subcommand(git_command())
            # `worktree` writes the NEW worktree's HEAD, seen from here (measured, git 2.53).
            # `switch`/`checkout` have ALREADY rewritten the tree and index by the time this hook
            # runs, so refusing here leaves main's HEAD over another branch's files (L3, 13:19Z:
            # 14 files staged under an unchanged HEAD). Those two are undone by post-checkout instead.
            if sub in ("worktree", "switch", "checkout"):
                continue
        if ref == "HEAD" and new.startswith("ref:") and new != MAIN:
            out.append(f"switching the live checkout to {new[4:]}")
        elif ref == "HEAD" and not new.startswith("ref:") and new != ZERO:
            out.append(f"detaching the live checkout's HEAD at {new[:12]}")
        elif ref == "refs/stash" and new != ZERO:
            out.append("stashing in the live checkout")
    return out


def undo_branch_checkout():
    """post-checkout, $3 = 1, in the live checkout: if HEAD left main, put it back with git's own
    two-way checkout (`git switch main`), which carries uncommitted service writes across exactly as
    the first switch did. Exit 1 so the command that moved it reports failure."""
    if not is_live() or _git("symbolic-ref", "-q", "HEAD") == "refs/heads/main":
        return 0
    if os.environ.get("HIVEMIND_LIVE_GIT_OK") == "1":
        print("live_repo_guard: ALLOWED by HIVEMIND_LIVE_GIT_OK=1: the live checkout left main", file=sys.stderr)
        return 0
    p = subprocess.run(["git", "switch", "-q", "main"], capture_output=True, text=True, timeout=120)
    if p.returncode == 0:
        print(f"live_repo_guard: REFUSED -- the live checkout may not leave main; it has been switched\n"
              f"  back to main (uncommitted changes carried, as git switch does).\n{FIX}", file=sys.stderr)
    else:
        print(f"live_repo_guard: REFUSED -- the live checkout left main and could NOT be switched back:\n"
              f"  {p.stderr.strip()}\n  Put it back by hand NOW: git -C <live> switch main\n{FIX}", file=sys.stderr)
    return 1


FIX = ("  The services write under ~/ai-workspace; work in a worktree instead:\n"
       "    git worktree add ~/.cache/hivemind-tmp/<name> -b <branch> main\n"
       "  (Jas only, deliberately: HIVEMIND_LIVE_GIT_OK=1 git ...)")


def main(argv):
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "ref-update":
        lines = sys.stdin.read().splitlines()
        if not is_live():
            return 0
        why = refusals(lines)
        if not why:
            return 0
        if os.environ.get("HIVEMIND_LIVE_GIT_OK") == "1":
            print(f"live_repo_guard: ALLOWED by HIVEMIND_LIVE_GIT_OK=1: {'; '.join(why)}", file=sys.stderr)
            return 0
        print(f"live_repo_guard: REFUSED -- {'; '.join(why)}.\n{FIX}", file=sys.stderr)
        return 1
    if mode == "branch-checkout":
        return undo_branch_checkout()
    if mode == "path-checkout":
        if is_live():
            print("live_repo_guard: WARNING -- a path checkout/restore just overwrote files in the LIVE\n"
                  "  checkout. No hook can refuse this beforehand. If it was not deliberate, check\n"
                  "  `git status` and the ledgers under workspace/ now.", file=sys.stderr)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
