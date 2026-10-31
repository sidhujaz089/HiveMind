#!/usr/bin/env python3
"""The SERIES limit on a role's task (Jas, 2026-10-01): after N of one task in a row, the role
takes the next task down its list once. 0 or absent = no limit. Both directions are pinned."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import task_routing as TR  # noqa: E402

FAILED = 0


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


def store(series):
    return {"roles": {"manager": {"kind": "model", "model": "m", "tasks": [
        {"task": "session_review", "interrupt": False, "teamwork": False, "series": series},
        {"task": "rca", "interrupt": False, "teamwork": False}]}}}


def main():
    items = [{"kind": "session-review", "role": "manager", "key": "s1"},
             {"kind": "session-review", "role": "manager", "key": "s2"},
             {"kind": "rca", "role": "manager", "key": "r1"}]
    kind = items[0]["kind"]
    got = TR.apply_series(store(2), items, {"manager": (kind, 2)})
    report("at the limit, the next task down goes first", [i["key"] for i in got], ["r1", "s1", "s2"])
    got = TR.apply_series(store(2), items, {"manager": (kind, 1)})
    report("under the limit, the order stands", [i["key"] for i in got], ["s1", "s2", "r1"])
    got = TR.apply_series(store(0), items, {"manager": (kind, 99)})
    report("series 0 is no limit", [i["key"] for i in got], ["s1", "s2", "r1"])
    got = TR.apply_series(store(2), items[:2], {"manager": (kind, 5)})
    report("nothing else waiting: the order stands", [i["key"] for i in got], ["s1", "s2"])
    print("test_task_series:", "FAILED" if FAILED else "ok")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
