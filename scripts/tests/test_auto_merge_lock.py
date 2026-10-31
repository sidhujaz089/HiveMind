#!/usr/bin/env python3
"""auto_merge.py's merge holds scripts/merge_lock.py -- the lock's first caller on the automated path.

test_merge_lock.py proves the LOCK. This proves the CALLER (08-l2-round-plan.md, L1 item 3): while
another lane holds the lock, auto-merge does not run `git merge` at all and says why; with the lock
free, the `git merge` runs INSIDE the lock; and the lock is released afterwards on success and on
failure. git and reviewq are faked, so nothing here touches a real branch; LANES_HOME points the lock
at a temp directory, so nothing here touches the lanes' real lock.
"""
import os
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

TMP = tempfile.mkdtemp(prefix="am-lock-")
os.environ["LANES_HOME"] = TMP
REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
sys.modules["reviewq"] = types.SimpleNamespace(pending=lambda: [], main=lambda argv: 0)
import auto_merge as am  # noqa: E402
import merge_lock  # noqa: E402

PASSED = FAILED = 0
FACTS = {"files": ["scripts/x.py"], "target": "scripts/tests/test_x.py"}


def check(name, ok, detail=""):
    global PASSED, FAILED
    PASSED += bool(ok)
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        {detail}"))


def fake_git(merge_rc):
    calls, committed = [], []

    def git(*args, **_kw):          # cwd/timeout accepted: merge_reviewed passes them (2026-10-29)
        held = merge_lock.holder() or {}
        calls.append((args[0], held.get("lane")))
        if args[0] == "commit" and "-F" in args:   # merge_reviewed reads its commit back (DS, lanes 17:36Z)
            committed.append(Path(args[args.index("-F") + 1]).read_text(encoding="utf-8"))
        if args[0] == "log":
            return 0, committed[-1] if committed else "", ""
        return (merge_rc, "", "conflict" if merge_rc else "") if args[0] == "merge" and "--abort" not in args \
            else (0, "", "")
    return git, calls


def main():
    holder = subprocess.Popen([sys.executable, "-c",
                               "import sys,time; sys.path.insert(0, sys.argv[1]); import merge_lock\n"
                               "with merge_lock.hold('L9', 'test holds it'):\n"
                               "    print('held', flush=True); time.sleep(6)", str(REPO / "scripts")],
                              env=os.environ.copy(), stdout=subprocess.PIPE, text=True)
    holder.stdout.readline()
    am.git, calls = fake_git(0)
    t0 = time.monotonic()
    ok, why = am.merge("coder/t1-x", FACTS, lock_timeout=1)
    check("with the lock held elsewhere, the merge is NOT made", not ok and not any(c[0] == "merge" for c in calls),
          (ok, why, calls))
    check("  and it says who holds it", "L9" in why, why)
    check("  and it gave up after its timeout, not at once", time.monotonic() - t0 >= 0.9)
    holder.wait()

    am.git, calls = fake_git(0)
    ok, why = am.merge("coder/t1-x", FACTS, lock_timeout=5)
    merges = [c for c in calls if c[0] == "merge"]
    check("with the lock free, the merge is made", ok and merges, (ok, why, calls))
    check("  INSIDE the lock, held as auto-merge", merges and merges[0][1] == "auto-merge", merges)
    check("  and the lock is released afterwards", not merge_lock.holder(), merge_lock.holder())

    am.git, calls = fake_git(1)
    ok, why = am.merge("coder/t1-x", FACTS, lock_timeout=5)
    check("a failed merge is reported failed", not ok, (ok, why))
    check("  and the lock is released after a failure too", not merge_lock.holder(), merge_lock.holder())

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
