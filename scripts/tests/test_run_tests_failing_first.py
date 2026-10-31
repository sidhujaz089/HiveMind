#!/usr/bin/env python3
"""run_tests.failing_first (2026-10-19 plan): suites that failed in the last week run first; the
set and the rest of the order are unchanged; an unreadable journal changes nothing.

Run: python3 scripts/tests/test_run_tests_failing_first.py
"""
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_tests as R  # noqa: E402

now = time.time()
with tempfile.TemporaryDirectory() as d:
    j = Path(d) / "j.jsonl"
    j.write_text("".join(json.dumps(r) + "\n" for r in [
        {"kind": "suite", "suite": "test_c.py", "rc": 1, "epoch": now - 3600},
        {"kind": "suite", "suite": "test_a.py", "rc": 0, "epoch": now - 3600},
        {"kind": "suite", "suite": "test_d.py", "rc": 1, "epoch": now - 30 * 86400}]) + "torn{\n")
    files = [Path(f"/x/test_{n}.py") for n in "abcd"]
    got = [f.name for f in R.failing_first(files, journal=j, now=now)]
    ok1 = got == ["test_c.py", "test_a.py", "test_b.py", "test_d.py"]
    ok2 = [f.name for f in R.failing_first(files, journal=Path(d) / "none", now=now)] == [f.name for f in files]
print("recent failure first, the rest in order:", ok1, got)
print("unreadable journal changes nothing:", ok2)
sys.exit(0 if ok1 and ok2 else 1)
