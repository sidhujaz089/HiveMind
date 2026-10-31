#!/usr/bin/env python3
"""coder.py's syntax gate names the KIND of fault it found (t469). It always said "the indentation
must match", and on 2026-09-29 the error was an unterminated string: Gemma re-indented, and broke
the line the same way again. Each kind gets its own hint; the old text only for indentation."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def gate(src):
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "m.py").write_text(src)
        return coder.py_syntax(Path(d), [{"path": "m.py"}])


def main():
    ok, msg = gate('x = "abc\n')
    report("an unterminated string is refused", ok, False)
    report("  and the hint is about the quote, not the indentation",
           ("string is not closed" in msg, "indentation must match" in msg), (True, False))
    ok, msg = gate("def f():\n  a = 1\n    b = 2\n")
    report("an indentation error keeps the indentation hint", "indentation must match" in msg, True)
    ok, msg = gate("f(1, 2\n")
    report("an unclosed bracket gets the bracket hint", "bracket is not balanced" in msg, True)
    ok, msg = gate("x = = 1\n")
    report("any other syntax error gets the general hint, not the indentation one",
           ("Read the line it names" in msg, "indentation must match" in msg), (True, False))
    report("a file that parses passes", gate("x = 1\n"), (True, ""))
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
