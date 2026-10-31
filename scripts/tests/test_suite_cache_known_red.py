#!/usr/bin/env python3
"""t1153: a suite declared in the failure ceiling that failed with the same inputs is reported as known
red and not re-run; changed inputs re-run it; an undeclared failure is never cached; a pass replaces
the red entry, so a fixed suite still comes off the ceiling. A temporary cache file; suite_key faked."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import run_tests as rt  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


rt.CACHE = Path(tempfile.mkdtemp()) / "suite-cache.json"
keys = {"test_red.py": "k1", "test_new.py": "k2", "test_ok.py": "k3"}
rt.suite_key = lambda p: (keys[Path(p).name], "faked")
ceiling = {"test_red.py"}
files = [rt.TESTS / n for n in keys]

rt.record_passes([("test_red.py", 1, 2.0, "line\nAssertionError: not built", False),
                  ("test_new.py", 1, 1.0, "boom", False),
                  ("test_ok.py", 0, 1.0, "", False)], ceiling)
hits = rt.cached_passes(files, ceiling)
report("a declared red with unchanged inputs is reused as red",
       (hits.get("test_red.py") or {}).get("red"), True)
report("its output tail is kept", "not built" in (hits.get("test_red.py") or {}).get("out", ""), True)
report("an UNDECLARED failure is never cached", "test_new.py" in hits, False)
report("a pass is still reused as a pass", (hits.get("test_ok.py") or {}).get("red"), None)
report("without the ceiling, no red is reused", "test_red.py" in rt.cached_passes(files), False)
report("off the ceiling, the red runs again", "test_red.py" in rt.cached_passes(files, set()), False)

keys["test_red.py"] = "k1-changed"
report("a changed input re-runs the red suite", "test_red.py" in rt.cached_passes(files, ceiling), False)

rt.record_passes([("test_red.py", 0, 2.0, "", False)], ceiling)
hits = rt.cached_passes(files, ceiling)
report("a pass replaces the red entry", (hits.get("test_red.py") or {}).get("red"), None)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
