#!/usr/bin/env python3
"""Fail-first oracle for t1159: preflight_brief must syntax-check a brief target BEFORE dispatch.

Red on main because `preflight_brief.syntax_check` does not exist yet -- a Python target with a
pre-existing syntax error (coder.py:3169 "unexpected EOF in multi-line statement") is dispatched
to the model, whose AST validator cannot operate on a file that does not parse.

Run: python3 scripts/tests/test_preflight_syntax.py   (no pytest; __main__ runs every test).
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]   # scripts/tests/ -> repo root (NOT parents[1])
sys.path.insert(0, str(REPO / "scripts"))

import preflight_brief  # noqa: E402


def _p(x):
    return str(x)


def test_clean_file_not_reported():
    """A .py that parses must NOT be reported: no false positive on clean code."""
    result = preflight_brief.syntax_check(["scripts/preflight_brief.py"])
    assert result == [], f"expected no failures for a clean file, got {result!r}"


def test_syntax_error_caught_and_named():
    """A .py with an unbalanced multi-line statement is reported, with ITS path and a non-empty
    error -- the coder.py:3169 'unexpected EOF in multi-line statement' shape."""
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "bad.py"
        bad.write_text("def f():\n    x = (1 + 2\n", encoding="utf-8")
        result = preflight_brief.syntax_check([_p(bad)])
    assert isinstance(result, list) and len(result) == 1, f"expected exactly one failure, got {result!r}"
    entry = result[0]
    assert entry["path"] == _p(bad), f"wrong path reported: {entry!r}"
    assert isinstance(entry["error"], str) and entry["error"].strip(), \
        f"error must be a non-empty string, got {entry!r}"


def test_only_the_bad_file_reported():
    """Clean + broken together: ONLY the broken one is returned; the clean one is not."""
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "bad.py"
        bad.write_text("if True:\n    return (\n", encoding="utf-8")
        result = preflight_brief.syntax_check(["scripts/preflight_brief.py", _p(bad)])
    bad_paths = {e["path"] for e in result}
    assert bad_paths == {_p(bad)}, f"only the broken file should be reported, got {bad_paths!r}"
    assert "scripts/preflight_brief.py" not in bad_paths, "a clean file must not be reported"


def test_non_python_paths_ignored():
    """A non-.py path is never parse-checked and never reported."""
    with tempfile.TemporaryDirectory() as d:
        md = Path(d) / "notes.md"
        md.write_text("not python at all {{{", encoding="utf-8")
        result = preflight_brief.syntax_check([_p(md)])
    assert result == [], f"non-.py paths must be ignored, got {result!r}"


def main():
    failures = 0
    for name in ("test_clean_file_not_reported", "test_syntax_error_caught_and_named",
                 "test_only_the_bad_file_reported", "test_non_python_paths_ignored"):
        try:
            globals()[name]()
            print(f"  PASS {name}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"  FAIL {name} ({type(e).__name__}): {e}")
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
