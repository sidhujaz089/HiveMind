#!/usr/bin/env python3
"""error_unknowns: one Manager task per NEW error shape, never per finding (2026-10-19)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import error_unknowns as e  # noqa: E402

failed = 0


def check(name, ok):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


a = {"kind": "truncated-output", "source": "workspace/run-logs/dispatch/p750-t1447-backup--prod--1792131101.log"}
b = {"kind": "truncated-output", "source": "workspace/run-logs/dispatch/p9-t2-other--prod--1792999999.log"}
c = {"kind": "oom", "source": "workspace/memory-guard.jsonl"}
check("two builds' logs of one kind are ONE shape", e.shape(a) == e.shape(b))
check("a different kind is a different shape", e.shape(a) != e.shape(c))
n = e.new_shapes([a, b, c], set())
check("one example per new shape", len(n) == 2)
check("a seen shape is never new again", e.new_shapes([a, b, c], set(n)) == {})
print("FAILED" if failed else "ok")
sys.exit(1 if failed else 0)
