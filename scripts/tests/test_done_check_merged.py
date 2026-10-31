#!/usr/bin/env python3
"""t1135: the board's Merged stage, WITH the time each branch was kept, oldest first.

Red on main: task_board has no merged_open() -- AttributeError.
"""
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import task_board  # noqa: E402

FAILS = []
NOW = time.mktime(time.strptime("2026-10-13T00:00:00", "%Y-%m-%dT%H:%M:%S"))


def sha8(text):
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def build_repo(tmp, tasks):
    """tasks: {task: (verdict, at)} -> writes the three files the board reads."""
    (tmp / "scripts").mkdir(parents=True, exist_ok=True)
    (tmp / "workspace" / "briefs").mkdir(parents=True, exist_ok=True)
    (tmp / "workspace").mkdir(parents=True, exist_ok=True)
    loop, runs, verdicts = [], [], []
    for i, (tid, (verdict, at)) in enumerate(sorted(tasks.items())):
        brief_body = "TASK: %s\n" % tid
        name = "brief-p%03d-x.txt" % i
        (tmp / "workspace" / "briefs" / name).write_bytes(brief_body.encode())
        loop.append("p%03d-x|%s|x" % (i, name))
        branch = "coder/brief-" + sha8(brief_body)
        runs.append({"branch": branch, "outcome": "ACCEPTED"})
        if verdict is not None:
            verdicts.append({"branch": branch, "verdict": verdict, "at": at})
    (tmp / "scripts" / "autonomy_loop.sh").write_text("\n".join(loop) + "\n", encoding="utf-8")
    (tmp / "workspace" / "agent-runs.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in runs), encoding="utf-8")
    (tmp / "workspace" / "review-verdicts.jsonl").write_text(
        "".join(json.dumps(v) + "\n" for v in verdicts), encoding="utf-8")


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def test_returns_merged_tasks_oldest_first():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        build_repo(tmp, {"t2222": ("kept", "2026-10-10T00:00:00"),
                         "t1111": ("kept", "2026-10-01T00:00:00"),
                         "t3333": ("kept", "2026-10-05T00:00:00")})
        got = task_board.merged_open(repo=tmp, now=NOW)
        check(isinstance(got, list), "merged_open must return a list")
        check([t for t, _ in got] == ["t1111", "t3333", "t2222"],
              "not ordered oldest merge first: %r" % ([t for t, _ in got],))
        check(all(isinstance(at, str) for _, at in got),
              "the merge time must come back as the ledger's own string, for a brief's created field")


def test_only_a_kept_verdict_is_merged():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        build_repo(tmp, {"t1111": ("kept", "2026-10-10T00:00:00"),
                         "t9999": ("rejected", "2026-10-10T00:00:00"),
                         "t8888": (None, None)})
        ids = [t for t, _ in task_board.merged_open(repo=tmp, now=NOW)]
        check("t1111" in ids, "a kept verdict must be on the Merged stage")
        check("t9999" not in ids, "a rejected verdict is NOT merged: it must not be checked")
        check("t8888" not in ids, "a delivered branch with no verdict is in review, not merged")


def test_a_merge_older_than_the_kept_window_is_out():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        build_repo(tmp, {"t1111": ("kept", "2020-01-01T00:00:00"),
                         "t2222": ("kept", "2026-10-10T00:00:00")})
        ids = [t for t, _ in task_board.merged_open(repo=tmp, now=NOW)]
        check(ids == ["t2222"],
              "merged_open must honour the file's own KEPT_DAYS window (%s), not list a merge from 2020"
              % task_board.KEPT_DAYS)


def test_empty_board_is_empty():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / "scripts").mkdir(parents=True, exist_ok=True)
        (tmp / "workspace").mkdir(parents=True, exist_ok=True)
        check(task_board.merged_open(repo=tmp, now=NOW) == [],
              "a board with no ledgers must return no items, not invented ones")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except Exception as e:
                FAILS.append("%s raised %s: %s" % (name, type(e).__name__, e))
    for f in FAILS:
        print("FAIL:", f)
    print("test_done_check_merged: %d assertion(s) failed" % len(FAILS))
    sys.exit(1 if FAILS else 0)
