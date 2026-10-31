#!/usr/bin/env python3
"""task_routing.round_robin: one lane per task first, then round again (Jas, 2026-10-07).

Hermetic: a fake role store, no dispatcher, no files. One role holds every task, top down:
Building, RCA, Session review, Script review.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import task_routing as T  # noqa: E402

FAILED = 0


def check(label, got, want):
    global FAILED
    FAILED += got != want
    print(f"  [{'ok' if got == want else 'BAD'}] {label}: {got!r}")


D = {"roles": {"manager": {"kind": "model", "model": "swift", "tasks": [
    {"task": "building"}, {"task": "rca"}, {"task": "session_review"}, {"task": "script_review"}]}}}


def items(kind, n):
    return [{"kind": kind, "role": "manager", "key": f"{kind}{i}"} for i in range(n)]


def kinds(picked):
    out = {}
    for i in picked:
        out[i["kind"]] = out.get(i["kind"], 0) + 1
    return out


many = items("rca", 20) + items("session-review", 20) + items("script-review", 20)
got, won = T.round_robin(D, many, {}, 3)
check("3 lanes, 3 tasks waiting: one each, not three RCAs", kinds(got),
      {"rca": 1, "session-review": 1, "script-review": 1})
got, won = T.round_robin(D, many, {}, 7)
check("7 lanes: everyone twice, then the top of the list a third time", kinds(got),
      {"rca": 3, "session-review": 2, "script-review": 2})
got, won = T.round_robin(D, items("rca", 20), {}, 7)
check("only RCA waiting: RCA takes every lane", kinds(got), {"rca": 7})
got, won = T.round_robin(D, many, {"rca": 5}, 3)
check("RCA already holds 5: the free lanes go to the others first", kinds(got),
      {"session-review": 2, "script-review": 1})
got, won = T.round_robin(D, many, {}, 4, build_waiting=True)
check("a build waiting takes its turn (top of the list)", won, True)
check("  and the other three lanes go one each", kinds(got),
      {"rca": 1, "session-review": 1, "script-review": 1})
got, won = T.round_robin(D, many, {}, 8, build_waiting=True)
check("one build at most, however many lanes", sum(1 for _ in [won] if won), 1)
check("  the other seven round the three tasks", sum(kinds(got).values()), 7)
got, won = T.round_robin(D, [], {}, 4)
check("nothing waiting: nothing started", (got, won), ([], False))
print(f"{'FAILED' if FAILED else 'ok'}: {FAILED} failure(s)")
sys.exit(1 if FAILED else 0)
