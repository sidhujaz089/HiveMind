# Fail-first oracle for the scope_lint 'oracle-weak' structural check (t502).
#
# RED on main: scope_lint has no 'oracle-weak' check, so a shape-only oracle
# passes lint with no 'oracle-weak' finding and the assertion below fails.
# GREEN once chunk 2 adds the check: the shape-only oracle is flagged.
#
# Mirrors the proven setup + sl.lint convention in scripts/tests/test_scope_lint.py.
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]   # scripts/
REPO = SCRIPTS.parent                              # repository root
sys.path.insert(0, str(SCRIPTS))

import scope_lint as sl  # noqa: E402

FAILS = []


def block_text(chunks):
    return "answer\n```chunks\n" + json.dumps(list(chunks)) + "\n```\n"


def repo_root():
    return Path(tempfile.mkdtemp(prefix="t502-weak-"))


def classes(text, repo, execute=False):
    return sorted({c for c, *_ in sl.lint(text, repo, execute)[1]})


def test_shape_only_oracle_is_flagged_weak(tmp_root):
    # The t471/t370 shape: an oracle that makes the call and asserts nothing
    # about the value -- it never checks that the value is consumed downstream,
    # never the real distinct constants, never the body's required fields.
    weak_oracle = ("import sys\n"
                   "sys.path.insert(0, 'lib')\n"
                   "import mod\n"
                   "mod.f()\n")
    block = block_text([
        {"kind": "oracle", "file": "scripts/tests/test_mod.py",
         "oracle_text": weak_oracle,
         "red_because": "scope_lint has no oracle-weak check yet"},
        {"kind": "implementation", "file": "lib/mod.py",
         "target": "scripts/tests/test_mod.py",
         "single_change": "add f", "what_is_wrong": "f is missing",
         "done_when": "python3 scripts/tests/test_mod.py exits 0",
         "related_entries": ["oracle-weak"]},
    ])
    found = classes(block, tmp_root, execute=False)
    assert "oracle-weak" in found, (
        "expected a shape-only oracle to be flagged oracle-weak; got %r" % (found,)
    )


def test_strong_oracle_is_not_flagged_weak(tmp_root):
    # A behavioural oracle (asserts the real, distinct constant and that the
    # value is consumed) must NOT be flagged oracle-weak -- guards against an
    # over-broad check (defence against check-satisfied-by-dead-path).
    strong_oracle = ("import sys\n"
                     "sys.path.insert(0, 'lib')\n"
                     "import mod\n"
                     "r = mod.f()\n"
                     "assert r['status'] == 'active', 'status must be the real constant'\n"
                     "assert r['id'] != 'x', 'id must be the distinct constant'\n")
    block = block_text([
        {"kind": "oracle", "file": "scripts/tests/test_mod.py",
         "oracle_text": strong_oracle,
         "red_because": "scope_lint has no oracle-weak check yet"},
        {"kind": "implementation", "file": "lib/mod.py",
         "target": "scripts/tests/test_mod.py",
         "single_change": "add f", "what_is_wrong": "f is missing",
         "done_when": "python3 scripts/tests/test_mod.py exits 0",
         "related_entries": ["oracle-weak"]},
    ])
    found = classes(block, tmp_root, execute=False)
    assert "oracle-weak" not in found, (
        "a behavioural oracle must not be flagged oracle-weak; got %r" % (found,)
    )


def _run_case(name, fn):
    tmp = repo_root()
    try:
        fn(tmp)
        print("PASS", name)
        return True
    except AssertionError as e:
        print("FAIL", name, "-", e)
        FAILS.append(name)
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    _run_case("shape-only-oracle-is-flagged-weak", test_shape_only_oracle_is_flagged_weak)
    _run_case("strong-oracle-is-not-flagged-weak", test_strong_oracle_is_not_flagged_weak)
    if FAILS:
        print("oracle-weak oracle: %d failed: %s" % (len(FAILS), FAILS))
        sys.exit(1)
    print("oracle-weak oracle: all passed")
    sys.exit(0)
