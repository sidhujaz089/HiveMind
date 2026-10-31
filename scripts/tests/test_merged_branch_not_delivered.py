#!/usr/bin/env python3
"""2026-10-14: a coder/* branch already merged into main is not "waiting on Claude's review", deleted
or not (~20 merged tasks sat on Claude because only deletion counted as merged); an unmerged one is.
A throwaway git repo under tempfile; nothing live is read or written."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import task_board as T  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def git(repo, *a):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *a],
                   check=True, capture_output=True, timeout=60)


r = Path(tempfile.mkdtemp())
git(r, "init", "-q", "-b", "main")
(r / "a").write_text("1\n")
git(r, "add", "a"); git(r, "commit", "-qm", "base", "--no-verify")
for b in ("coder/brief-aaaa1111", "coder/brief-bbbb2222"):
    git(r, "checkout", "-qb", b)
    (r / b.split("-")[1]).write_text(b)
    git(r, "add", "-A"); git(r, "commit", "-qm", b, "--no-verify")
    git(r, "checkout", "-q", "main")
git(r, "merge", "-q", "--no-ff", "-m", "m", "coder/brief-aaaa1111", "--no-verify")
why = "DELIVERED, waiting on Claude's review: "
report("a merged branch that still exists is not waiting", T._branch_exists(r, why + "coder/brief-aaaa1111"), False)
report("an unmerged branch is waiting", T._branch_exists(r, why + "coder/brief-bbbb2222"), True)
report("a deleted branch is not waiting", T._branch_exists(r, why + "coder/brief-cccc3333"), False)
src = (ROOT / "scripts" / "auto_merge.py").read_text(encoding="utf-8")
report("auto_merge deletes the branch it merged", '"branch", "-d", branch' in src, True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
