#!/usr/bin/env python3
"""Fixtures for scripts/sweep.py --done on workspace/app-feedback/ notes (t322).

The same commit-before-delete guard that protects workspace/inbox/ must protect
workspace/app-feedback/: a note whose CURRENT bytes are not in HEAD must be refused,
so an uncommitted note is never destroyed by the sweep that handles it (the
pre-2026-08-12 failure scripts/sweep.py exists to prevent, now for the channel t322
actually sweeps).

Today --done is inbox-only and refuses an app-feedback path as 'escapes the inbox',
so case 1 below is RED: a committed app-feedback note is NOT retired.
"""
import shutil
import sys

from test_sweep import git, make_repo, sweep

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def main():
    r = make_repo()  # already creates workspace/app-feedback/README.md and commits it

    print("--done must retire an app-feedback note only when its bytes are in HEAD:")
    # 1. committed app-feedback note -> must actually go (RED today: refused as inbox-escape)
    n = r / "workspace" / "app-feedback" / "2026-08-22T051301Z--new-idea--test.md"
    n.write_text("from the phone\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "add app-feedback note")
    out = sweep(r, "--done", "workspace/app-feedback/2026-08-22T051301Z--new-idea--test.md")
    report("committed app-feedback note is retired (rc 0)", out.returncode, 0)
    report("  and is gone from the tree", n.is_file(), False)
    report("  and the receipt points at history",
           "committed copy remains in history" in out.stdout, True)

    # 2. the load-bearing case: uncommitted note -> refused, and the only copy survives
    n2 = r / "workspace" / "app-feedback" / "uncommitted-note.md"
    n2.write_text("the only copy of this edit\n")
    out = sweep(r, "--done", "workspace/app-feedback/uncommitted-note.md")
    report("uncommitted app-feedback note is refused (rc 1)", out.returncode, 1)
    report("  and the only copy survives", "only copy" in n2.read_text(), True)

    # 3. the README keeps the app-feedback directory alive in git
    out = sweep(r, "--done", "workspace/app-feedback/README.md")
    report("app-feedback README.md is refused (rc 1)", out.returncode, 1)

    # 4. an escaping path is still refused, with the existing 'escapes the inbox' text
    out = sweep(r, "--done", "../../etc/passwd")
    report("an escaping path is still refused (rc 1)", out.returncode, 1)
    report("  and says 'escapes the inbox'", "escapes the inbox" in out.stderr, True)

    # 5. an outbox file is still named as outbox, not swallowed by the widened channel
    (r / "workspace" / "outbox" / "sent.md").write_text("a message Code sent\n")
    out = sweep(r, "--done", "workspace/outbox/sent.md")
    report("an outbox path still names the outbox",
           "OUTBOX, not the inbox" in out.stderr, True)

    shutil.rmtree(r, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
