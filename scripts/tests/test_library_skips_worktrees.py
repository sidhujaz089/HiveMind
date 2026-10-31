#!/usr/bin/env python3
"""Fail-first oracle for t2114: `library.py verify` must not police another checkout.

Measured 2026-10-25: `library.py verify` walked the WHOLE repo, so a file inside
`.claude/worktrees/swift-jr-evaluation-f91c98/` -- a different checkout of this repository, with its
own working tree and its own in-flight scripts -- was reported as UNREGISTERED here. Two consequences
and both are wrong: this checkout's verdict is polluted by somebody else's work in progress, and the
honest fix a reader would reach for ("just register it") adds an entry for a file this checkout cannot
run.

A coder sandbox is already exempt by design (it is a copy). A git worktree is the same case: another
checkout's tree is not this checkout's script list.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import library  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="t2114-"))
    try:
        (tmp / "scripts").mkdir()
        (tmp / "scripts" / "real.py").write_text("print(1)\n", encoding="utf-8")
        wt = tmp / ".claude" / "worktrees" / "another-checkout" / "scripts"
        wt.mkdir(parents=True)
        (wt / "inflight.py").write_text("print(2)\n", encoding="utf-8")
        wt2 = tmp / ".claude" / "worktrees" / "another-checkout" / "workspace" / "run-logs"
        wt2.mkdir(parents=True)
        (wt2 / "DRAFT_test_x.py").write_text("print(3)\n", encoding="utf-8")

        real_repo = library.REPO
        library.REPO = tmp
        try:
            found = library.on_disk()
        finally:
            library.REPO = real_repo

        check("this checkout's own script is seen", "scripts/real.py" in found, True)
        check("a file inside another checkout's worktree is NOT seen",
              [p for p in found if "another-checkout" in p], [])
        check("the scan still finds exactly one script here", len(found), 1)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
