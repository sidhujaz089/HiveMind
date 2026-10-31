#!/usr/bin/env python3
"""The Lanes tab's sweep line separates skipped, expected and NEW failures (Jas, 2026-10-19)."""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lane_status as L  # noqa: E402

allowed = set(json.loads((L.REPO / "workspace" / "test-failures-ceiling.json").read_text())["allowed_to_fail"])
known = sorted(allowed)[0]
with tempfile.TemporaryDirectory() as t:
    p = Path(t) / "j.jsonl"
    rows = [{"kind": "start", "total": 10, "epoch": time.time() - 60, "by": "session end"},
            {"kind": "cached", "n": 5, "red": 2},
            {"kind": "suite", "suite": known, "rc": 1},
            {"kind": "suite", "suite": "test_definitely_not_declared_x.py", "rc": 1},
            {"kind": "suite", "suite": "test_passing_x.py", "rc": 0}]
    p.write_text("\n".join(json.dumps(r) for r in rows))
    line = L.sweep_progress(p)
checks = [("who started it", line.startswith("session end:")),
          ("skipped are counted as done", "8/10 suites" in line),
          ("skipped expected failures are said", "2 of them expected failures" in line),
          ("a new failure is named", "1 NEW failure(s) (test_definitely_not_declared_x.py)" in line),
          ("an expected failure is not called new", "1 expected failure(s) re-run" in line)]
for name, ok in checks:
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
print(line)
sys.exit(0 if all(ok for _, ok in checks) else 1)
