#!/usr/bin/env python3
"""scope_lint.py: an implementation chunk that edits a file with NO line of that file cited, no
SYMBOLS entry and no append:true is brief-unanchored -- t502's shape. A throwaway git repository in
a temp dir; the real repository is not touched."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import scope_lint as sl  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=60)


def block(*chunks):
    return "answer\n```chunks\n" + json.dumps(list(chunks)) + "\n```\n"


def impl(target, **kw):
    return {"kind": "implementation", "file": "lib/mod.py", "target": target,
            "single_change": "add f", "what_is_wrong": "f is missing",
            "done_when": f"python3 {target} exits 0", "related_entries": ["x"], **kw}


def found(text, repo):
    """The findings scope_lint returns, as (class, chunk index, file, evidence) -- the tuple shape
    record() prints into the ledger as `chunk {i} {f}: {ev}`."""
    return sl.lint(text, repo, execute=False)[1]


def classes(text, repo):
    return sorted({c for c, _i, _f, _ev in found(text, repo)})


def main():
    tmp = Path(tempfile.mkdtemp(prefix="lint-anchor-"))
    try:
        repo = tmp / "repo"
        (repo / "lib").mkdir(parents=True)
        (repo / "scripts" / "tests").mkdir(parents=True)
        (repo / "lib" / "mod.py").write_text("def g():\n    return 1\n", encoding="utf-8")
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "-A")
        git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
        T = "scripts/tests/test_mod.py"
        oracle = {"kind": "oracle", "file": T, "red_because": "f is missing",
                  "oracle_text": "import sys\nsys.path.insert(0, 'lib')\nimport mod\n"
                                 "assert hasattr(mod, 'f'), 'f missing'\n"}
        bare = block(oracle, impl(T))
        report("a chunk editing a file that cites no line of it, names no symbol and sets no append "
               "is brief-unanchored (t502's brief: the builder had no window, re-emitted the file "
               "from guess five times)",
               "brief-unanchored" in classes(bare, repo), True)
        report("  and the finding carries the chunk index and file record() books it under",
               [(i, f) for c, i, f, _ev in found(bare, repo) if c == "brief-unanchored"],
               [(1, "lib/mod.py")])
        report("  its evidence says what is missing, so the Manager can act on it",
               all("no line" in ev and "SYMBOLS" in ev for _c, _i, _f, ev in found(bare, repo)
                   if _c == "brief-unanchored"), True)
        report("an anchored brief is NOT reported: a SYMBOLS entry naming a symbol in the file",
               "brief-unanchored" in classes(block(oracle, impl(T, symbols=["g"])), repo), False)
        report("  a SYMBOLS entry that is not in the file is brief-anchor's case, not this one",
               "brief-unanchored" in classes(block(oracle, impl(T, symbols=["nope"])), repo), False)
        report("  a citation of a line that is inside the file",
               "brief-unanchored" in classes(block(oracle, impl(
                   T, single_change="in lib/mod.py:1 add f next to g")), repo), False)
        report("  and append:true", "brief-unanchored" in
               classes(block(oracle, dict(impl(T), append=True)), repo), False)
        report("a chunk editing a file that does not exist is brief-unsatisfiable's case, not this one",
               "brief-unanchored" in classes(block(oracle, dict(impl(T), file="lib/new_page.html")),
                                             repo), False)
        report("a citation PAST the end of the file is not an anchor: it is still reported, so a "
               "brief cannot clear the check by inventing a line number",
               "brief-unanchored" in classes(block(oracle, impl(
                   T, single_change="in lib/mod.py:99 add f")), repo), True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
