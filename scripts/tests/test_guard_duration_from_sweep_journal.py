#!/usr/bin/env python3
"""t1324: guard-duration answers must be computed from sweep-journal.jsonl, not quoted
from prose (RCA 2cbd3724265f.md item 2). Pins a computer over the rows the suite writes.
Loads run_tests.py by file path; fixture rows are literals; no live state, no subprocess."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "scripts" / "run_tests.py"

def _load():
    spec = importlib.util.spec_from_file_location("run_tests", str(SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_source_defines_the_computer():
    assert "def worst_case_hook_secs" in SRC.read_text(encoding="utf-8"), \
        "t1324: run_tests.py defines no worst_case_hook_secs(rows)"

def test_computes_worst_and_typical_from_fixture_rows():
    try:
        mod = _load()
    except Exception as e:
        raise AssertionError(f"run_tests.py cannot be imported as a module: {type(e).__name__}: {e}")
    fn = getattr(mod, "worst_case_hook_secs", None)
    assert callable(fn), "no callable worst_case_hook_secs"
    rows = [{"suite": "a", "secs": 10}, {"suite": "b", "secs": 250}, {"suite": "a", "secs": 40}]
    got = fn(rows)
    assert isinstance(got, dict), f"must return a dict, got {type(got).__name__}"
    assert got.get("worst") == 250, f"worst over per-suite totals (a=10+40=50, b=250) should be 250, got {got.get('worst')!r}"
    assert got.get("typical") == 150, f"typical (mean of suite totals, (50+250)/2) should be 150, got {got.get('typical')!r}"
    assert got.get("total") == 300, f"total should be 300, got {got.get('total')!r}"

if __name__ == "__main__":
    _fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    _failed = 0
    for _n, _f in _fns:
        try:
            _f()
            print(f"PASS {_n}")
        except Exception as e:
            _failed += 1
            print(f"FAIL {_n}: {type(e).__name__}: {e}")
    print(f"\n{len(_fns) - _failed}/{len(_fns)} passed")
    raise SystemExit(1 if _failed else 0)
