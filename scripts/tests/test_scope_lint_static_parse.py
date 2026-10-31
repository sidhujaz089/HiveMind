#!/usr/bin/env python3
"""scope_lint's STATIC oracle-crash check (t686): a non-parsing oracle is flagged
oracle-crash by the static path (sl.lint with execute=False / --no-run), not only by
run_oracles executing it in a worktree. A SyntaxError is a property of the text; deciding
it needs no execution. The t669 oracle (2026-10-05) was exactly this: a real
SyntaxError caught only because the lint executed it.

Red on main for the missing change: static() does not ast.parse oracle_text, so a
non-parsing oracle produces no oracle-crash finding under execute=False.

Throwaway repo under tempfile; no live state touched; no model calls; every
subprocess has a timeout.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import scope_lint as sl  # noqa: E402

T = "scripts/tests/test_mod.py"

IMPL = {"kind": "implementation", "file": "lib/mod.py", "target": T,
        "single_change": "add f", "what_is_wrong": "f is missing",
        "done_when": f"python3 {T} exits 0", "related_entries": ["oracle-crash"]}

RED_ORACLE = "def (:\n"                    # a real SyntaxError, the t669 shape
GOOD_ORACLE = ("import sys\n"
               "sys.path.insert(0, 'lib')\n"
               "import mod\n"
               "assert hasattr(mod, 'f'), 'f missing'\n")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=120)


def block(*chunks):
    return "answer\n```chunks\n" + json.dumps(list(chunks)) + "\n```\n"


def classes(text, repo):
    return sorted({cls for cls, *_ in sl.lint(text, repo, execute=False)[1]})


def main():
    tmp = Path(tempfile.mkdtemp(prefix="scope-lint-static-"))
    code = 0
    try:
        repo = tmp / "repo"
        (repo / "lib").mkdir(parents=True)
        (repo / "scripts" / "tests").mkdir(parents=True)
        (repo / "lib" / "mod.py").write_text("def g():\n    return 1\n", encoding="utf-8")
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "-A")
        git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")

        # 1. A non-parsing oracle is oracle-crash in the STATIC path (execute=False).
        got = classes(block({"kind": "oracle", "file": T, "oracle_text": RED_ORACLE,
                             "red_because": "f is missing"}, IMPL), repo)
        if "oracle-crash" not in got:
            print(f"FAIL: a non-parsing oracle is not oracle-crash under execute=False: {got}")
            code = 1
        else:
            print("ok 1: a non-parsing oracle is oracle-crash in the static path")

        # 2. A parsing oracle is NOT oracle-crash in the static path (no false positive).
        got = classes(block({"kind": "oracle", "file": T, "oracle_text": GOOD_ORACLE,
                             "red_because": "f is missing"}, IMPL), repo)
        if "oracle-crash" in got:
            print(f"FAIL: a parsing oracle is oracle-crash under execute=False: {got}")
            code = 1
        else:
            print("ok 2: a parsing oracle is not oracle-crash in the static path")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
