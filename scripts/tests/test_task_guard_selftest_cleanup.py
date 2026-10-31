#!/usr/bin/env python3
"""The guard self-test's cleanup: exact name, idempotent, and it never touches a sibling.

WHY. scripts/tests/test_task_write_guard.py:74 collects what to delete with
glob(f"{tid}*.md") -- a PREFIX, so a probe minted as t999 also deletes t9990--anything.md,
which is somebody else's task. f.unlink() at line 80 assumes the file is still there;
workspace/tasks/t697--task-guard-self-test-creates-deletes-t692-guard.md:1 records the crash that
produced ("races with a live taskstore add ... the add crashed with FileNotFoundError"). And
three probes are sitting on disk right now (workspace/tasks/t984--guard-self-test-delete-me.md,
t987--guard-self-test-delete-me.md, t990--guard-self-test-delete-me.md, all `status: queued`),
which is what "it does not retire what it mints" looks like.

RED ON MAIN FOR ONE REASON: scripts/tests/test_task_write_guard.py defines no
cleanup_selftest_task. It is a test module (module level is defs only), so importing it by path
executes nothing -- the import is safe; the missing name is the finding.

Temp dirs and fakes only. No live workspace/tasks, no model call, no subprocess.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "tests" / "test_task_write_guard.py"

# Real constants, not placeholders: the title the self-test mints (test_task_write_guard.py:71)
# and the slug taskstore.py gives it on disk (the real file names on disk).
SELFTEST_TITLE = "GUARD SELF TEST -- delete me"
SELFTEST_FILE = "t999--guard-self-test-delete-me.md"
DECOY_FILE = "t9990--a-real-task.md"          # id shares the prefix "t999"
OTHER_FILE = "t9991--another-real-task.md"    # id shares it too
QUEUE_ROWS = (
    "# Queue\n"
    "\n"
    "320. t9990 -- a real task that must survive\n"
    "321. t999 -- " + SELFTEST_TITLE + "\n"
    "322. t9991 -- another real task\n"
)


def load_under_test():
    spec = importlib.util.spec_from_file_location("twg_under_test", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sandbox(root):
    """A fake workspace/tasks: three task files, one QUEUE.md, the probe among them."""
    (root / "QUEUE.md").write_text(QUEUE_ROWS, encoding="utf-8")
    (root / SELFTEST_FILE).write_text(
        "---\nid: t999\ntitle: " + SELFTEST_TITLE + "\n---\n\n## Notes\n\n## Attachments\n",
        encoding="utf-8")
    (root / DECOY_FILE).write_text("---\nid: t9990\n---\n", encoding="utf-8")
    (root / OTHER_FILE).write_text("---\nid: t9991\n---\n", encoding="utf-8")


def test_cleanup_function_exists():
    mod = load_under_test()
    fn = getattr(mod, "cleanup_selftest_task", None)
    assert fn is not None, (
        "scripts/tests/test_task_write_guard.py has no cleanup_selftest_task(tasks_dir, filename, "
        "tid); the cleanup is inline inside main() at lines 77-83 and cannot be tested")
    assert callable(fn)


def test_removes_the_file_the_self_test_created():
    mod = load_under_test()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sandbox(root)
        removed = mod.cleanup_selftest_task(root, SELFTEST_FILE, "t999")
        assert not (root / SELFTEST_FILE).exists(), (
            "the probe it minted is still on disk -- this is what leaves t984/t987/t990 behind")
        assert [p.name for p in removed] == [SELFTEST_FILE], (
            f"cleanup must report exactly what it removed, got {[p.name for p in removed]}")


def test_leaves_a_sibling_whose_id_shares_the_prefix():
    """The prefix-glob bug: t999*.md matches t9990--... and t9991--... -- other people's tasks."""
    mod = load_under_test()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sandbox(root)
        mod.cleanup_selftest_task(root, SELFTEST_FILE, "t999")
        assert (root / DECOY_FILE).exists(), (
            "cleanup deleted a task whose id merely starts with the same prefix "
            "(t9990) -- the prefix glob at test_task_write_guard.py:74")
        assert (root / OTHER_FILE).exists(), (
            "cleanup deleted a task whose id merely starts with the same prefix (t9991)")


def test_is_idempotent_when_the_file_is_already_gone():
    """t697's recorded crash: unlink on a file that raced away raised FileNotFoundError."""
    mod = load_under_test()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sandbox(root)
        (root / SELFTEST_FILE).unlink()
        removed = mod.cleanup_selftest_task(root, SELFTEST_FILE, "t999")
        assert list(removed) == [], (
            f"a missing probe file must be a no-op that reports nothing removed, got {list(removed)}")


def test_queue_row_of_the_probe_goes_and_the_real_rows_stay():
    mod = load_under_test()
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        sandbox(root)
        mod.cleanup_selftest_task(root, SELFTEST_FILE, "t999")
        q = (root / "QUEUE.md").read_text(encoding="utf-8")
        assert "t999 -- " + SELFTEST_TITLE not in q, "the probe's queue row must be removed"
        assert "320. t9990 -- a real task that must survive" in q, (
            "a sibling queue row was removed; only the probe's own row is the target")
        assert "322. t9991 -- another real task" in q, (
            "a sibling queue row was removed; only the probe's own row is the target")
        assert q.startswith("# Queue"), "the queue header must survive"


def main():
    fails = []
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  [ok]    {name}")
        except AssertionError as e:
            print(f"  [BAD]    {name}: {e}")
            fails.append(name)
        except Exception as e:  # noqa: BLE001 -- a crash is a finding, not a skip
            print(f"  [BAD]    {name}: CRASH {type(e).__name__}: {e}")
            fails.append(name)
    print("test_task_guard_selftest_cleanup:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
