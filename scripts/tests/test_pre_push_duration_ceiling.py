#!/usr/bin/env python3
"""t1324: the pre-push hook's header must state its duration budget and that a run is resumable.
Source: RCA 2cbd3724265f.md item 3 -- the guard's duration exceeds every caller budget and
the header tells no reader the ceiling. Reads the tracked hook; no live state."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "hooks" / "pre-push"

def _text():
    return HOOK.read_text(encoding="utf-8")

def test_header_states_resumability_and_a_budget():
    t = _text().lower()
    assert "resumable" in t, "t1324: the pre-push header never says a full run is resumable"
    assert "budget" in t, "t1324: the pre-push header states no duration budget"

def test_header_gives_a_numeric_ceiling():
    assert re.search(r"\b\d+\s*(s|sec|secs|seconds|minutes)\b", _text()), \
        "t1324: the pre-push header gives no numeric duration ceiling"

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
