#!/usr/bin/env python3
"""scripts/pnumber.py: p numbers never repeat, across main and every worktree (2026-09-28)."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import pnumber  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=60)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "r"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
        report("first claim is above the highest row seen", pnumber.claim(repo, 59), 60)
        report("a second writer that saw the same rows does NOT get 60 again",
               pnumber.claim(repo, 59), 61)
        report("a block of 3 is consecutive and after the last", pnumber.claim(repo, 0, count=3), 62)
        report("the next claim follows the block", pnumber.claim(repo, 0), 65)
        wt = Path(tmp) / "wt"
        git(repo, "worktree", "add", "-q", "-b", "b", str(wt))
        report("a worktree shares the same counter", pnumber.claim(wt, 59), 66)
        report("rows added by hand above the counter are respected", pnumber.claim(repo, 100), 101)
        plain = Path(tmp) / "plain"
        plain.mkdir()
        report("outside git: highest seen + 1, nothing stored", pnumber.claim(plain, 7), 8)
        report("  and again the same (no shared state to protect)", pnumber.claim(plain, 7), 8)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
