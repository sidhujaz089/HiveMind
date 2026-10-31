#!/usr/bin/env python3
"""t1324: every push attempt must leave a durable machine-readable row (RCA item 5).
The 03:03 false success is unrecoverable because the reason lived in an ephemeral file;
scripts/session_end.py decides a push from `git("push")`'s exit code at :974-:981 (read
turn 2026-10-20). This fixture pins a writer the push site calls, recording ref state, not
an exit code. Loads session_end.py by file path; all paths from tempfile; no live state."""
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "scripts" / "session_end.py"

def _load():
    spec = importlib.util.spec_from_file_location("session_end", str(SRC))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_module_exposes_the_writer():
    mod = _load()
    assert callable(getattr(mod, "git_push_attempt_row", None)), "session_end.py defines no callable git_push_attempt_row"
    assert callable(getattr(mod, "record_push_attempt", None)), "session_end.py defines no callable record_push_attempt"

def test_writer_records_refs_and_reason():
    mod = _load()
    tmp = tempfile.TemporaryDirectory(prefix="pushledger-")
    row = mod.git_push_attempt_row(repo=tmp.name, rc=0, ref_before="a" * 40, ref_after="b" * 40, reason="ok", caller="session_end")
    assert isinstance(row, dict), f"writer must return the row dict, got {type(row).__name__}"
    for k in ("who", "what", "when", "ref_before", "ref_after", "rc"):
        assert k in row, f"row missing {k!r}: {sorted(row)}"
    out = Path(tmp.name) / "push-attempts.jsonl"
    mod.record_push_attempt(str(out), row)
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, f"expected exactly one JSONL row, got {len(lines)}"
    assert json.loads(lines[0])["ref_after"] == "b" * 40

def test_the_push_site_calls_the_writer():
    t = SRC.read_text(encoding="utf-8")
    i = t.index('git("push")')
    window = t[max(0, i - 2000):i + 2000]
    assert "record_push_attempt" in window, "the push site (session_end.py:974-981) must call record_push_attempt"

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
