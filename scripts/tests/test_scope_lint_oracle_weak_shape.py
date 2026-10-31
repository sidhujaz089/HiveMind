#!/usr/bin/env python3
"""t458 oracle (2026-10-03): scope_lint's oracle-weak check must flag shape-only oracles.

The merged check (scope_lint.py:125-129) fires only through _never_fails
(scope_lint.py:67): the oracle must have no assert, raise, or exit call at all.
Oracles whose only assertions are shape/presence -- substring-in-source,
isinstance, len == N, == a container type, a bare assert_called() -- pin no
value and still print clean. t209, t471, t297, t370 and the t502 preflight
objections (qwen3.8:27b and gemma4:26b, 2026-10-02/03) are exactly this shape,
refused downstream as brief-unsatisfiable. The check must flag those too, while
an oracle with any real value assertion stays unflagged, and an
assertion-free oracle stays flagged (the existing _never_fails half, which
this change must not touch).

Red on main: test_shape_only_flagged and test_bare_assert_called_flagged fail
-- the lint prints clean for those fixtures. After the fix all four pass.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINT = ROOT / "scripts" / "scope_lint.py"

# Every assertion is a shape/presence check; no value is pinned.
SHAPE_ONLY = (
    "#!/usr/bin/env python3\n"
    "import pathlib\n"
    "\n"
    "def test_shape():\n"
    "    src = pathlib.Path(\"data.txt\").read_text()\n"
    "    assert \"some_call(\" in src\n"
    "    assert isinstance(src, str)\n"
    "    assert len(src) == 42\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    test_shape()\n"
)

# The only pin is a bare assert_called(): call made, body never checked.
BARE_CALLED = (
    "#!/usr/bin/env python3\n"
    "import unittest.mock\n"
    "\n"
    "def test_call():\n"
    "    m = unittest.mock.Mock()\n"
    "    m.f()\n"
    "    m.assert_called()\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    test_call()\n"
)

# One assertion pins a real value: this oracle is not weak and must stay clean.
VALUE_PINNED = (
    "#!/usr/bin/env python3\n"
    "\n"
    "def test_value():\n"
    "    result = 42\n"
    "    assert result == 42\n"
    "    assert isinstance(result, int)\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    test_value()\n"
)

# No assertions at all: the existing _never_fails half must keep flagging it.
NO_ASSERTS = (
    "#!/usr/bin/env python3\n"
    "\n"
    "def test_nada():\n"
    "    x = 1 + 1\n"
    "    print(x)\n"
    "\n"
    "if __name__ == \"__main__\":\n"
    "    test_nada()\n"
)


def make_answer(oracle_text):
    chunks = [
        {
            "kind": "oracle",
            "file": "scripts/tests/test_example.py",
            "oracle_text": oracle_text,
            "red_because": "fixture",
        },
        {
            "kind": "implementation",
            "file": "scripts/scope_lint.py",
            "target": "scripts/tests/test_example.py",
            "single_change": "add a function",
            "what_is_wrong": "missing function",
            "done_when": "python3 scripts/tests/test_example.py exits 0",
            "related_entries": ["t458"],
            "behaviour_oracle": "existing tests still pass",
        },
    ]
    return (
        "## (c) SCOPE\n\nfixture for the oracle-weak shape check\n\n"
        "```chunks\n" + json.dumps(chunks) + "\n```\n"
    )


def run_lint(oracle_text):
    f = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
    f.write(make_answer(oracle_text))
    f.close()
    name = f.name
    try:
        p = subprocess.run(
            [sys.executable, str(LINT), name, "--task", "t458", "--no-run"],
            capture_output=True, text=True, timeout=120,
        )
        return (p.stdout or "") + (p.stderr or "")
    finally:
        Path(name).unlink(missing_ok=True)


class T(unittest.TestCase):
    def test_shape_only_flagged(self):
        out = run_lint(SHAPE_ONLY)
        self.assertIn("oracle-weak", out,
                      "shape-only oracle must produce an oracle-weak finding: %r" % out)

    def test_bare_assert_called_flagged(self):
        out = run_lint(BARE_CALLED)
        self.assertIn("oracle-weak", out,
                      "bare assert_called oracle must produce an oracle-weak finding: %r" % out)

    def test_value_pinned_not_flagged(self):
        out = run_lint(VALUE_PINNED)
        self.assertNotIn("oracle-weak", out,
                         "oracle with a value assertion must not be flagged: %r" % out)

    def test_no_asserts_still_flagged(self):
        out = run_lint(NO_ASSERTS)
        self.assertIn("oracle-weak", out,
                      "assertion-free oracle must stay flagged (the _never_fails half): %r" % out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
