#!/usr/bin/env python3
"""t1324: AGENTS.md must define push success by the remote ref, never a pipe's exit.
Source: workspace/rca/2cbd3724265f.md (transcript 3182/3187): `git push 2>&1 | tail -3`
returned tail's exit 0 while five commits stayed local. Reads the tracked doc in a
worktree; no live state, no subprocess, no network."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "AGENTS.md"

def _text():
    return DOC.read_text(encoding="utf-8")

def test_doc_names_the_ref_check_as_the_verdict():
    t = _text()
    assert "rev-list" in t, "t1324: AGENTS.md never names git rev-list as how a push is judged"

def test_doc_bans_trailing_formatters_on_a_guard_or_push():
    t = _text()
    assert "PIPESTATUS" in t, "t1324: AGENTS.md carries no ban on piping a guard/push to a trailing formatter"

def test_doc_points_at_the_verifier_module():
    t = _text()
    assert "git_push_verified" in t, "t1324: AGENTS.md does not name scripts/git_push_verified.py"

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
