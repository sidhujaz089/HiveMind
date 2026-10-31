#!/usr/bin/env python3
"""t1146 oracle: Jas's pins stay above everything; a failed task goes to the front of the
UNPINNED rest.

Run: python3 scripts/tests/test_task_pinning.py   (pytest is not installed here).

Red on main for one reason: projects/app-repo/taskstore.py has no is_pinned/set_pinned, and
move(tid, 'top') inserts at index 0 -- above every pinned task (its line 713, ids.insert(0, ...)).

EVERY test works in a throwaway tree: ts.TASKS / ts.DONE / ts.QUEUE / ts.LOCK are redirected
into a tempfile.TemporaryDirectory before any write, exactly as test_task_queue.py does (see the
docstring of _resolved_path at taskstore.py:253 -- it resolves from TASKS at CALL time, so the
real workspace/tasks/RESOLVED.md is never touched). No subprocess, no model, no fixed /tmp.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests -> repo root
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
import taskstore as ts                                # noqa: E402

PIN_X = "- [x] pinned -- stays above everything not pinned"


def tree(tmp):
    """Point the store at a throwaway tree. Called first in every test, so no test can reach
    workspace/tasks."""
    d = Path(tmp) / "tasks"
    ts.TASKS = d
    ts.DONE = d / "done"
    ts.QUEUE = d / "QUEUE.md"
    ts.LOCK = d / ".lock"
    d.mkdir(parents=True, exist_ok=True)


def four():
    """Four machine tasks (source='test' is not by_jas, so add() appends -- taskstore.py:413)."""
    return [ts.add(name, source="test") for name in ("alpha", "beta", "gamma", "delta")]


def hand_flag(tid, tick="x"):
    """Write the pin checkbox straight into the task FILE, using no helper, to prove the flag's
    one source of truth is the task file (D-39). add() guarantees '## Notes' exists (line 409)."""
    meta, _ = ts.find(tid)
    assert meta, f"{tid} does not exist"
    p = Path(meta["path"])
    text = p.read_text(encoding="utf-8")
    assert "## Notes" in text, f"{tid} has no Notes section to carry the flag"
    ts.write_atomic(p, text.replace("## Notes",
                                    f"## Notes\n- [{tick}] pinned -- stays above everything "
                                    f"not pinned", 1))


def test_flag_is_read_from_the_task_file():
    """is_pinned mirrors is_big_project (taskstore.py:377): True ticked, False unticked/absent,
    None for an unknown task -- an unknown task is never silently 'not pinned'."""
    with tempfile.TemporaryDirectory() as tmp:
        tree(tmp)
        a = ts.add("alpha", source="test")
        assert ts.is_pinned(a) is False, "a task with no pinned box reads as pinned"
        hand_flag(a, "x")
        assert ts.is_pinned(a) is True, "an [x] box in the file is not read as pinned"
        hand_flag(a, "X")
        assert ts.is_pinned(a) is True, "an [X] box is a tick too (see _BIG, taskstore.py:374)"
        hand_flag(a, " ")
        assert ts.is_pinned(a) is False, "an [ ] box must read as not pinned"
        assert ts.is_pinned("t99999") is None, "unknown task must read None, not False"


def test_set_pinned_writes_the_flag_into_the_file():
    """The flag is WRITTEN to the task file, as a body checkbox -- the same shape as
    BIG_PROJECT_LINE (taskstore.py:373). It must NOT become a front-matter key: _rewrite drops
    keys its list at taskstore.py:444 does not name."""
    with tempfile.TemporaryDirectory() as tmp:
        tree(tmp)
        a = ts.add("alpha", source="test")
        assert ts.set_pinned(a, True) is True
        _meta, body = ts.find(a)
        assert PIN_X in body, f"flag not written to the task file:\n{body}"
        assert ts.is_pinned(a) is True
        assert ts.set_pinned(a, False) is True
        _meta, body = ts.find(a)
        assert PIN_X not in body, f"unpin left the ticked flag in the file:\n{body}"
        assert ts.is_pinned(a) is False
        assert ts.set_pinned("t99999", True) is False, "pinning a missing task must fail loudly"


def test_a_failure_move_to_top_lands_below_every_pin():
    """The rule dispatch_loop relies on (task text: it keeps calling move(tid,'top') and lands
    below the pins with no change of its own). Setup: a,b,c,d in order; pin a and c."""
    with tempfile.TemporaryDirectory() as tmp:
        tree(tmp)
        a, b, c, d = four()
        assert ts.read_queue() == [a, b, c, d], "fixture: add() appends"
        assert ts.set_pinned(a, True) is True
        assert ts.set_pinned(c, True) is True
        assert ts.read_queue() == [a, c, b, d], (
            f"pinning must place the task in the pinned block at the head, keeping existing "
            f"pins in order: {ts.read_queue()}")
        assert ts.move(d, "top") is True
        assert ts.read_queue() == [a, c, d, b], (
            f"an unpinned move-to-top must land directly below the last pin, not above it: "
            f"{ts.read_queue()}")
        assert ts.read_queue().index(a) < ts.read_queue().index(c), "pins reordered"


def test_pins_keep_their_relative_order_under_repeated_moves():
    """Jas's order among pins survives repeated automatic moves (two failed builds in a row)."""
    with tempfile.TemporaryDirectory() as tmp:
        tree(tmp)
        a, b, c, d = four()
        ts.set_pinned(a, True)
        ts.set_pinned(c, True)
        assert ts.read_queue() == [a, c, b, d]
        assert ts.move(b, "top") is True
        assert ts.read_queue() == [a, c, b, d], (
            f"unpinned b has no pin to jump over, so it goes below the pins: {ts.read_queue()}")
        assert ts.move(d, "top") is True
        assert ts.read_queue() == [a, c, d, b], f"{ts.read_queue()}"
        q = ts.read_queue()
        assert q.index(a) < q.index(c), f"repeated move-to-top reordered Jas's pins: {q}"


def test_a_pinned_task_never_lands_below_an_unpinned_one():
    """'bottom' on a pinned task is the end of the PINNED block, not the end of the queue --
    otherwise one move undoes a pin."""
    with tempfile.TemporaryDirectory() as tmp:
        tree(tmp)
        a, b, c, d = four()
        ts.set_pinned(a, True)
        ts.set_pinned(b, True)
        assert ts.read_queue() == [a, b, c, d]
        assert ts.move(a, "bottom") is True
        assert ts.read_queue() == [b, a, c, d], (
            f"a pinned task sent to the bottom dropped below the unpinned tasks: "
            f"{ts.read_queue()}")


def test_unpin_drops_the_task_into_the_unpinned_block_at_the_top():
    """Unpinning must not leave a now-unpinned task sitting above the pins that remain."""
    with tempfile.TemporaryDirectory() as tmp:
        tree(tmp)
        a, b, c, d = four()
        ts.set_pinned(a, True)
        ts.set_pinned(c, True)
        assert ts.read_queue() == [a, c, b, d]
        assert ts.set_pinned(a, False) is True
        assert ts.is_pinned(a) is False
        assert ts.read_queue() == [c, a, b, d], (
            f"unpinning a left it above the still-pinned {c}: {ts.read_queue()}")


def test_move_unchanged_when_nothing_is_pinned():
    """No pins in play -> today's behaviour exactly (no smuggling of unrelated changes)."""
    with tempfile.TemporaryDirectory() as tmp:
        tree(tmp)
        a, b, c, d = four()
        assert ts.move(c, "top") is True
        assert ts.read_queue() == [c, a, b, d]
        # "up" from the top is a no-op today (insert at max(0, i - 1)); the oracle used to expect it to
        # move down, which no build that keeps "up" untouched could satisfy (Claude, t1146 objection).
        assert ts.move(c, "up") is True
        assert ts.read_queue() == [c, a, b, d]
        assert ts.move(c, "down") is True
        assert ts.read_queue() == [a, c, b, d]
        assert ts.move(c, "down") is True
        assert ts.read_queue() == [a, b, c, d]
        assert ts.move("t99999", "top") is False, "moving a missing task must fail loudly"
        assert ts.read_queue() == [a, b, c, d]


def main():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:                                       # noqa: BLE001
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
