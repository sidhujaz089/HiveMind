#!/usr/bin/env python3
"""jsonl_cache: parse once, then only appended lines (2026-10-03, the Hub's 67 MB ledger reads)."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jsonl_cache as jc  # noqa: E402

FAILS = []


def ck(label, got, want):
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))
    if not ok:
        FAILS.append(label)


with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "x.jsonl"
    ck("a missing file is empty", jc.rows(p), [])
    p.write_text('{"a": 1}\n{"a": 2}\n')
    ck("first read parses every line", [r["a"] for r in jc.rows(p)], [1, 2])
    with p.open("a") as f:
        f.write('{"a": 3}\nnot json\n{"a": 4')            # a torn line, then a write in progress
    r, bad = jc.rows(p, torn=True)
    ck("appended lines are added; a half-written last line waits", [x["a"] for x in r], [1, 2, 3])
    ck("  an unparseable line is counted", bad, 1)
    with p.open("a") as f:
        f.write('}\n')
    ck("the finished line arrives on the next call", [x["a"] for x in jc.rows(p)], [1, 2, 3, 4])
    ck("each call returns a new list (a caller's append cannot leak)", jc.rows(p) is jc.rows(p), False)
    q = Path(d) / "y.jsonl"
    q.write_text('{"a": 9}\n')
    os.replace(q, p)                                         # replaced: a new inode, shorter
    ck("a replaced file is read again from the top", [x["a"] for x in jc.rows(p)], [9])

print("test_jsonl_cache:", "FAILED" if FAILS else "ok")
sys.exit(1 if FAILS else 0)
