#!/usr/bin/env python3
"""t1586 -- two checkouts must never mint one task id.

`taskstore.py` allocated `max(id)+1` from the task directory of the checkout it ran in. On
2026-10-24 a task filed from a worktree took t1554, which the live store already used: the worktree
was missing every task that only exists on main. The file was untracked so nothing was lost, but a
merged record would be two files under one id and one of them invisible in the plan.

The rule this suite pins: a worktree allocates from the LIVE checkout's id space (the parent of
`git rev-parse --git-common-dir`, the way check_workspace finds it), and the id it takes is
recorded there under that store's lock, so a second mint -- in this checkout or another -- cannot
take the same number. The task FILE still lands in the checkout that filed it; only the number is
shared.

Seeded, never live: two temp checkouts, one shaped like the live store (tasks up to t1554) and one
shaped like a worktree that has just one task of its own.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "projects" / "app-repo")]
import taskstore as ts  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  (got {got!r}, want {want!r})"))


def checkout(root, ids=()):
    """Point the store at a checkout-shaped tree and seed it with the ids it already holds."""
    root = Path(root)
    ts.WORKSPACE = root
    ts.TASKS, ts.DONE = root / "workspace" / "tasks", root / "workspace" / "tasks" / "done"
    ts.QUEUE, ts.LOCK = ts.TASKS / "QUEUE.md", ts.TASKS / ".lock"
    ts.ATTACH = root / "workspace" / "attachments"
    ts.TASKS.mkdir(parents=True, exist_ok=True)
    ts.DONE.mkdir(parents=True, exist_ok=True)
    for tid in ids:
        (ts.TASKS / f"{tid}--seeded.md").write_text(
            f"---\nid: {tid}\ntitle: seeded\narea: workspace\nstatus: queued\n---\n\n## Notes\n",
            encoding="utf-8")
    ts.write_queue(list(ids))
    return root


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    live = checkout(tmp / "live", [f"t{n}" for n in range(1548, 1555)])   # ...t1554, the incident
    ts.LIVE_ROOT = live
    live_id = ts.add("a task filed in the live checkout", area="workspace", source="test")
    report("the live checkout mints the number after its own highest", live_id, "t1555")

    # THE WORKTREE: its own store holds one task (t001), so the old allocator would have said t002 --
    # which is the whole bug. It must take the live space's next number instead.
    wt = checkout(tmp / "worktree", ["t001"])
    ts.LIVE_ROOT = live                       # its git common dir is the live checkout's
    wt_id = ts.add("a task filed from a worktree", area="workspace", source="test")
    report("a worktree does not mint from its own near-empty store", wt_id != "t002", True)
    report("  nor does it reuse the id the live checkout just took", wt_id != live_id, True)
    report("  it takes the next number in the live checkout's space", wt_id, "t1556")

    # A SECOND mint in the same worktree: the first number must be recorded somewhere shared, or
    # the two mints both read max(t1554) and collide. This is the half a lock alone cannot do.
    again = ts.add("a second task from the same worktree", area="workspace", source="test")
    report("a second mint from the same worktree does not collide with the first",
           again != wt_id, True)
    report("  it is the number after it", again, "t1557")

    report("the worktree's task file landed in the worktree, not the live checkout",
           ((wt / "workspace" / "tasks" / f"{wt_id}--a-task-filed-from-a-worktree.md").exists(),
            (live / "workspace" / "tasks" / f"{wt_id}--a-task-filed-from-a-worktree.md").exists()),
           (True, False))
    report("the minted id is in the worktree's QUEUE.md",
           wt_id in (wt / "workspace" / "tasks" / "QUEUE.md").read_text(encoding="utf-8"), True)
    report("the live checkout's QUEUE.md was not given the worktree's task",
           wt_id in (live / "workspace" / "tasks" / "QUEUE.md").read_text(encoding="utf-8"), False)

    # A fixture that repoints the store at a temp tree (every taskstore suite does) keeps its own
    # space: only a checkout whose own root differs from the live one goes to the live store.
    fixture = checkout(tmp / "fixture", ["t500", "t501"])
    ts.WORKSPACE, ts.LIVE_ROOT = fixture, fixture
    report("a fixture pointed at its own store allocates from it",
           ts.add("seeded", area="workspace", source="test"), "t502")

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
