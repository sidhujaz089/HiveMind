#!/usr/bin/env python3
"""t1556: an untracked suite that was DELETED from the live checkout must reach "untracked-resolved".

WHAT HAPPENED (verified on main 2026-10-25): scripts/tests/test_chat_lane.py is gone from the live
checkout -- `ls` says No such file or directory, `git ls-files --error-unmatch` matches nothing, and
`git log --all -- <path>` is empty (never committed on any branch). The task that exists to close on
exactly that says: "When the file is tracked, OR GONE, the scheduler records 'untracked-resolved' and
notes this task." Days later the ledger has the OPEN row and no close row (workspace/red-on-main.jsonl:51
is the only line naming the suite), so red_on_main.open_untracked() still reports it open every tick.

WHY IT NEVER FIRED: the resolve sweep in dispatch_loop.main_red_hold asks git ONE question -- is the
file tracked? -- and resolves on "yes". The DELETE half of its own contract is unimplemented, and the
suite that tests this path exercises only the git-tracks case, so the guard is green over a dead branch.
The contract is in writing in two places: red_on_main's module docstring ("when the file becomes
tracked or is deleted") and the resolve comment in dispatch_loop ("the file is committed now, or
deleted from the live checkout").

Fixes in dispatch_loop.py: a pure helper `untracked_settled(open_rows, tracked, root)` that resolves
BOTH ways -- git tracks it, OR it is not on disk -- and main_red_hold calling it.

Isolation: no file is ever written into scripts/tests/ (that is the very defect this task removes, and
coder.py copies untracked files into every sandbox). The deleted case uses suite names that exist in
neither git nor the working tree; the present case lives under a tempfile root passed as `root=`. The
live ledger and the live task store are never touched: red_on_main is stubbed in sys.modules and
resolve_untracked only appends to a local list. No subprocess, no model call, no live server.
"""
import shutil
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import dispatch_loop as D  # noqa: E402

fails = []
GONE = "test_zz_t1556_gone.py"        # in neither git nor the working tree
PRESENT = "test_zz_t1556_present.py"  # on disk under `root` only, never under REPO


def ck(label, got, want):
    print(f"  [{'ok' if got == want else 'FAIL'}]    {label} -> {got!r}")
    if got != want:
        fails.append(f"{label}: expected {want!r}, got {got!r}")


def main():
    # ---- THE CONTRACT, IN WRITING: the resolve path must cover the deleted half ----------
    ck("red_on_main's docstring says a deleted file resolves",
       "or is deleted" in __import__("red_on_main").__doc__, True)
    ck("dispatch_loop's resolve comment says a deleted file resolves",
       "deleted from the live checkout" in (REPO / "scripts" / "dispatch_loop.py")
       .read_text(encoding="utf-8"), True)

    # ---- 1. the helper exists --------------------------------------------------------
    have = hasattr(D, "untracked_settled")
    ck("dispatch_loop exposes untracked_settled", have, True)
    if not have:
        print("       untracked_settled is not defined in dispatch_loop.py; every check below"
              " depends on it. That is the missing change, not a bug in this test.")

    # ---- 2-6. the helper's rules -----------------------------------------------------
    if have:
        root = Path(tempfile.mkdtemp())
        try:
            tdir = root / "scripts" / "tests"
            tdir.mkdir(parents=True)
            (tdir / PRESENT).write_text("# present, untracked\n", encoding="utf-8")
            (tdir / "test_committed.py").write_text("# committed\n", encoding="utf-8")
            open_rows = {GONE: {"event": "untracked-oracle", "task": "t1556"},
                         PRESENT: {"event": "untracked-oracle", "task": "t9001"},
                         "test_committed.py": {"event": "untracked-oracle", "task": "t9002"}}

            ck("a DELETED suite settles (the half the sweep is missing)",
               sorted(D.untracked_settled(open_rows, set(), root)), [GONE])
            ck("  and its open row comes through, so the task id survives",
               D.untracked_settled(open_rows, set(), root).get(GONE, {}).get("task"), "t1556")
            ck("a COMMITTED suite settles (the half that already works)",
               sorted(D.untracked_settled(open_rows, {"scripts/tests/test_committed.py"}, root)),
               [GONE, "test_committed.py"])
            # NEGATIVE: an untracked suite that is STILL ON DISK must stay open. Resolving it
            # closes the owner task while every build is still refusing to start on it -- the
            # repeated-refusal stall this ledger exists to prevent.
            ck("an untracked suite that is STILL ON DISK does not settle",
               sorted(D.untracked_settled({PRESENT: open_rows[PRESENT]}, set(), root)), [])
            ck("an empty open set settles nothing", D.untracked_settled({}, set(), root), {})
            # FAIL OPEN (L-015): git unable to answer is not evidence a file is absent.
            ck("git unable to answer (None) settles nothing (fail open, L-015)",
               D.untracked_settled(open_rows, None, root), {})
            # The DEFAULT root is this repo's checkout, where neither zz name exists: a real
            # untracked-then-deleted suite resolves with no root passed, as main_red_hold calls it.
            ck("no root arg: defaults to REPO and finds the gone suite",
               sorted(D.untracked_settled({GONE: open_rows[GONE]}, set())), [GONE])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # ---- 7. INTEGRATION: the live sweep must ACT on it, not just compute it ----------
    # A helper nobody calls fixes nothing. Driven the way test_untracked_suite.py drives
    # main_red_hold: a stub red_on_main in sys.modules, RED_ACTIONS True, restored in a finally.
    stub = types.ModuleType("red_on_main")
    resolved = []
    stub.handle_untracked = lambda untracked, **k: []
    stub.handle = lambda red, **k: list(red)
    stub.open_untracked = lambda **k: {GONE: {"event": "untracked-oracle", "task": "t1556"}}
    stub.resolve_untracked = lambda s, task=None, **k: resolved.append((s, task))
    stub.WINDOW_S = 3600
    real_rom = sys.modules.get("red_on_main")
    sys.modules["red_on_main"] = stub
    real_actions = D.RED_ACTIONS
    try:
        if (REPO / "scripts" / "tests" / GONE).exists():
            fails.append(f"precondition: {GONE} exists in the live checkout -- remove it")
            print(f"  [FAIL]    precondition: {GONE} must not exist in the repo")
        elif D.tracked_tests() is None:
            print("  [skip]  sweep: git cannot answer here (the coder sandbox)")
        else:
            D.RED_ACTIONS = True
            held = D.main_red_hold([], lambda t: True)
            ck("the live sweep resolves the deleted suite, with its task id",
               resolved, [(GONE, "t1556")])
            ck("  and resolving it holds no build", held, None)
    finally:
        D.RED_ACTIONS = real_actions
        if real_rom is not None:
            sys.modules["red_on_main"] = real_rom
        else:
            sys.modules.pop("red_on_main", None)

    for x in fails:
        print("FAIL:", x)
    print("test_chat_lane_close:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
