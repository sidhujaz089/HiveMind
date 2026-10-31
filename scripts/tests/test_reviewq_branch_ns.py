#!/usr/bin/env python3
"""t647 fail-first oracle: a ref that exists ONLY under refs/remotes/origin/ must be
seen by reviewq.branch_exists -- not read as a flat 'MISSING'.

Red on main: branch_exists (reviewq.py:176-181) runs
    git -C <REPO> rev-parse --verify <name>
which resolves only LOCAL refs (refs/heads, refs/tags, short SHAs) and never
refs/remotes/origin/*. A remote-tracking review branch therefore reads as MISSING.
The fix resolves the ref across refs/heads + refs/remotes (+ worktrees), so a
remote-tracking ref is FOUND. This suite asserts that behaviour; it is red until
the lookup is fixed.

Do not invent a function, module, or path: everything referenced appears in the
source (reviewq.branch_exists, reviewq.REPO). Self-contained: temp repo only.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]      # scripts/tests/ -> scripts/ -> repo root
sys.path.insert(0, str(ROOT / "scripts"))
import reviewq


def git(repo, *args, timeout=60):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=timeout)


def make_remote_only_repo():
    """A scratch repo with one commit and a ref ONLY under refs/remotes/origin/."""
    d = Path(tempfile.mkdtemp(prefix="t647_branch_ns_"))
    git(d, "init", "-q")
    git(d, "-c", "user.email=t@t", "-c", "user.name=t",
        "commit", "--allow-empty", "-q", "-m", "seed")
    sha = git(d, "rev-parse", "HEAD").stdout.strip()
    # a remote-tracking-only ref: no local branch of this name
    git(d, "update-ref", "refs/remotes/origin/coder/tTEST", sha)
    return d


PASSED = 0
FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = (got is want)
    PASSED += ok
    FAILED += (not ok)
    print(f"  [{'ok' if ok else 'FAIL'}] {name}: {got!r} (want {want!r})")


def main():
    repo = make_remote_only_repo()
    old = reviewq.REPO
    try:
        reviewq.REPO = repo
        # THE DEFECT, asserted as the fixed behaviour: a ref present only under
        # refs/remotes/origin/ must be FOUND (today it reads as a flat MISSING).
        report("a refs/remotes/origin/-only ref is seen by branch_exists (not a flat MISSING)",
               reviewq.branch_exists("coder/tTEST"), True)
        # Guard: a ref in no namespace must still read as absent (fix must not over-match).
        report("a ref in no namespace is still absent",
               reviewq.branch_exists("coder/no-such-branch"), False)
    finally:
        reviewq.REPO = old
        shutil.rmtree(repo, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
