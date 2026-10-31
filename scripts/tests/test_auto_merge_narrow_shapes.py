#!/usr/bin/env python3
"""Part A (2026-10-19): the NARROW check accepts the three normal shapes of a narrow change
and still refuses what the brief never named.

~30 delivered branches were refused as "outside the named symbols" because symbol_spans() only
spanned defs and classes in the BASE file: a named constant, a NEW named function, and the import
a new symbol needs all read as outside. Each case is a real two-commit git repo, so the hunk
numbers are git's own, not a reimplementation of them.

Run: python3 scripts/tests/test_auto_merge_narrow_shapes.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auto_merge  # noqa: E402

BASE = '''import re

LIMIT = 300
TAIL = "x"


def judge(branch):
    return re.match("x", branch)


def other():
    return 1
'''


def verdict(after, names):
    """(outside, tolerated) exactly as judge() computes them, on a throwaway repo."""
    with tempfile.TemporaryDirectory(prefix="am-narrow-") as d:
        def g(*a):
            return subprocess.run(["git", *a], cwd=d, capture_output=True, text=True, check=True).stdout
        g("init", "-q", "-b", "main")
        g("config", "user.email", "t@t"); g("config", "user.name", "t")
        (Path(d) / "m.py").write_text(BASE)
        g("add", "."); g("commit", "-qm", "base", "--no-verify")
        base = g("rev-parse", "HEAD").strip()
        (Path(d) / "m.py").write_text(after)
        g("commit", "-qam", "build", "--no-verify")
        branch = g("rev-parse", "HEAD").strip()
        real = auto_merge.git

        def git_here(*a, cwd=None, timeout=300):
            r = subprocess.run(["git", *a], cwd=d, capture_output=True, text=True, timeout=timeout)
            return r.returncode, r.stdout, r.stderr
        auto_merge.git = git_here
        try:
            names = set(names)
            spans = auto_merge.symbol_spans(BASE, names)
            new = auto_merge.new_symbol_spans(BASE, after, names)
            out = auto_merge.hunks_outside(base, branch, "m.py", spans, new, after)
            return out, bool(out) and auto_merge.outside_tolerance(BASE, after, out)
        finally:
            auto_merge.git = real


def accepted(after, names):
    out, tol = verdict(after, names)
    return not out or tol


def test_editing_a_named_constant_is_inside():
    assert accepted(BASE.replace("LIMIT = 300", "LIMIT = 500"), ["LIMIT"])


def test_editing_an_unnamed_constant_is_outside():
    assert not accepted(BASE.replace("TAIL = \"x\"", "TAIL = \"y\""), ["LIMIT"])


def test_a_new_named_function_is_inside():
    after = BASE + "\n\ndef roll_up(x):\n    return x\n"
    assert accepted(after, ["roll_up"])


def test_a_new_named_function_with_its_comment_block_is_inside():
    after = BASE + "\n\n# why roll_up exists, in two\n# lines of comment\nLEDGER2 = 1\n\n\ndef roll_up(x):\n    return x\n"
    assert accepted(after, ["roll_up", "LEDGER2"])


def test_import_plus_new_named_symbols_elsewhere_is_inside():
    after = BASE.replace("import re\n", "import re\nimport json\n") + "\n\n# a new helper\ndef roll_up(x):\n    return json.dumps(x)\n"
    assert accepted(after, ["roll_up"])


def test_a_comment_cannot_smuggle_unnamed_code():
    after = BASE + "\n\n# a comment\ndef sneaky(x):\n    return x\n"
    assert not accepted(after, ["judge"])


def test_a_new_unnamed_function_is_outside():
    after = BASE + "\n\ndef sneaky(x):\n    return x\n"
    assert not accepted(after, ["judge"])


def test_edit_in_named_function_plus_added_import_is_accepted():
    after = BASE.replace("import re\n", "import re\nimport json\n").replace(
        'return re.match("x", branch)', 'return json.dumps(re.match("x", branch))')
    assert accepted(after, ["judge"])


def test_an_edit_in_an_unnamed_function_is_outside():
    after = BASE.replace("return 1", "return 2")
    assert not accepted(after, ["judge"])


def test_named_function_plus_lowercase_module_statement_is_refused():
    after = BASE.replace("import re\n", "import re\ncache = {}\n")
    assert not accepted(after, ["judge"])


def test_a_new_named_constant_is_inside():
    after = BASE.replace('TAIL = "x"\n', 'TAIL = "x"\nNEW_LEDGER = "a.jsonl"\n')
    assert accepted(after, ["NEW_LEDGER"])


def test_helper_constants_in_one_block_with_a_named_new_function_are_inside():
    # 2026-10-20, coder/brief-362fa23a: OWNER_PRIORITY, NEXT_ACTOR, TASK_ID, QUEUE beside two named
    # new functions in ONE inserted block read as outside, although each part alone is allowed.
    after = BASE.replace('TAIL = "x"\n', 'TAIL = "x"\nTASK_ID = re.compile(r"t\\d+")\n'
                         'QUEUE = (\n    "a.md"\n)\n\n\ndef gaps(text):\n    return TASK_ID.findall(text)\n')
    assert accepted(after, ["gaps"])


def test_an_unnamed_helper_function_in_that_block_is_still_outside():
    after = BASE.replace('TAIL = "x"\n', 'TAIL = "x"\nTASK_ID = 1\n\n\ndef _helper(x):\n    return x\n\n\n'
                         'def gaps(text):\n    return _helper(text)\n')
    assert not accepted(after, ["gaps"])


def test_a_lowercase_module_variable_in_that_block_is_still_outside():
    after = BASE.replace('TAIL = "x"\n', 'TAIL = "x"\n_stdout = None\n\n\ndef gaps(text):\n    return text\n')
    assert not accepted(after, ["gaps"])


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS {n}")
        except Exception as e:                                      # noqa: BLE001
            bad += 1
            print(f"FAIL {n}: {type(e).__name__}: {e}")
    print(f"{len(tests) - bad} passed, {bad} failed")
    sys.exit(1 if bad else 0)
