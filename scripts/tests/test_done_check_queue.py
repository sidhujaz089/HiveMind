#!/usr/bin/env python3
"""t1135 item 2: one done-check item per task on the board's Merged stage, oldest merge first.

Red on main: dispatch_loop has no done_check_items() -- AttributeError.
"""
import inspect
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import dispatch_loop  # noqa: E402

FAILS = []
MERGED = [("t2222", "2026-10-10T00:00:00"), ("t1111", "2026-10-01T00:00:00"),
          ("t3333", "2026-10-05T00:00:00")]


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def test_one_item_per_merged_task_oldest_first():
    items = dispatch_loop.done_check_items(MERGED)
    check(isinstance(items, list), "done_check_items must return a list")
    check(len(items) == 3, "expected 3 items for 3 merged tasks, got %d" % len(items))
    check([i.get("key") for i in items] == ["t1111", "t3333", "t2222"],
          "items are not ordered oldest merge first: %r" % ([i.get("key") for i in items],))
    for i in items:
        check(i.get("kind") == "done-check", "item kind is %r, expected 'done-check'" % (i.get("kind"),))
        check(i.get("created") == dict(MERGED).get(i.get("key")),
              "item %s carries created=%r; it must carry that task's merge time so task_routing.order()"
              " sorts it oldest first" % (i.get("key"), i.get("created")))
        check(i.get("chunk") == "done-check--" + str(i.get("key")),
              "chunk must be unique per task: %r" % (i.get("chunk"),))
        check(str(i.get("path", "")).startswith("workspace/tasks/")
              and str(i.get("path", "")).endswith("/" + str(i.get("key"))),
              "path should point at the task file, got %r" % (i.get("path"),))
        check(i.get("brief") is None, "a done-check item carries no brief: %r" % (i.get("brief"),))


def test_role_and_model_come_from_the_store_never_hardcoded():
    it = dispatch_loop.done_check_items([("t1111", "2026-10-01T00:00:00")])[0]
    for field in ("role", "model", "interrupt", "level"):
        check(field in it, "item is missing the routing field %r, which routed() supplies" % field)
    expected = dispatch_loop.routed("done-check", created="2026-10-01T00:00:00", key="t1111",
                                    chunk="done-check--t1111", path="workspace/tasks/t1111",
                                    brief=None, brief_sha256=None)
    for field in ("kind", "role", "model", "interrupt", "level"):
        check(it.get(field) == expected[field],
              "%s is %r; it must be routed('done-check')'s %r, not a hard-coded value"
              % (field, it.get(field), expected[field]))
    check(isinstance(it.get("model"), str) and bool(it.get("model")),
          "model must be a non-empty string taken from the store")
    if not it.get("role"):
        check("done_check" in it["model"],
              "with no owner the model field must say the task is unowned, got %r" % (it["model"],))


def test_no_merged_tasks_makes_no_items():
    check(dispatch_loop.done_check_items([]) == [],
          "an empty Merged stage must produce no items, not invented ones")


def test_priority_items_actually_calls_it():
    src = inspect.getsource(dispatch_loop.priority_items)
    check("done_check_items" in src,
          "priority_items() never calls done_check_items() -- a function nobody queues is a decoration")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                FAILS.append("%s raised %s: %s" % (name, type(e).__name__, e))
    for f in FAILS:
        print("FAIL:", f)
    print("test_done_check_queue: %d assertion(s) failed" % len(FAILS))
    sys.exit(1 if FAILS else 0)
