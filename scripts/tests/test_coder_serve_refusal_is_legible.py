#!/usr/bin/env python3
"""t1493: coder.py's read/refuse layer must say WHY a file was not shown.

Two distinct causes collapse into the string "no such file" today:

  (a) the model asked for a bare basename of a file that IS served -- "coder.py" for
      "projects/local-inference/coder.py". The sandbox root has no top-level coder.py, so a
      plain existence check reports the served file as absent.
  (b) the file exists in the repository but was not placed in this build's sandbox.

A model that trusts its tools reads "no such file" in both cases as "the brief names paths
that do not exist, so this job is impossible". That is the cascade in t1493 / p755-t1463:
attempt 6 was refused three brief-named test paths this way, and attempt 7 refused the real
target -- projects/local-inference/coder.py, which WAS in --files -- with the same words.

RED ON MAIN for the stated reason, not for a bug of its own: `readable` (coder.py:583-603)
has no basename resolution, and its existence check (599-600) runs before its scope check
(601-602), so both cases answer "no such file". Cases 3-5 pass on main; they are the guards
that keep the fix narrow.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests -> repo root
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder                                          # noqa: E402

SERVED = "projects/local-inference/coder.py"          # the shape of a --files entry
REPO_ONLY = "scripts/tests/test_coder_need_scope.py"  # real repo file, never in the sandbox

FAILURES = []


def check(what, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + what + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILURES.append(what)


def sandbox_with(*rel_paths):
    """A throwaway sandbox containing exactly these files. Nothing outside it is touched."""
    root = Path(tempfile.mkdtemp(prefix="t1493-sandbox-"))
    for rel in rel_paths:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x = 1\n", encoding="utf-8")
    return root


def test_basename_of_a_served_file_is_served():
    """(a) A bare basename naming the served file resolves to it -- it is not absent."""
    root = sandbox_with(SERVED)
    try:
        p, why = coder.readable(root, Path(SERVED).name, [SERVED])
        check("bare 'coder.py' resolves to the served path", p == root / SERVED,
              f"got path={p!r} reason={why!r}")
        check("  with no reason given", why == "", repr(why))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_repo_file_outside_the_sandbox_is_not_called_absent():
    """(b) A repo file that is not in this sandbox is out of scope -- say that, not "missing"."""
    assert (ROOT / REPO_ONLY).is_file(), f"fixture premise broken: {REPO_ONLY} not in repo"
    root = sandbox_with("projects/local-inference/other.py")
    try:
        p, why = coder.readable(root, REPO_ONLY, ["projects/local-inference/other.py"])
        check("a repo file absent from the sandbox is refused", p is None, repr(p))
        low = (why or "").lower()
        check("the refusal says out-of-scope, not absent",
              "sandbox" in low and "no such file" not in low, repr(why))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_genuinely_absent_path_still_says_no_such_file():
    """The words a caller asserts on must survive: scripts/tests/test_coder_need_target.py:58
    asserts "no such file" in the reason. A build that re-names the honest refusal is wrong."""
    root = sandbox_with(SERVED)
    try:
        p, why = coder.readable(root, "scripts/tests/test_definitely_not_here.py", [SERVED])
        check("an absent path is still refused", p is None, repr(p))
        check("and 'no such file' is kept for it",
              "no such file" in (why or "").lower(), repr(why))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_a_basename_that_is_not_served_is_not_served():
    """The resolution is narrow. A basename naming nothing in the file list gets nothing,
    and must not be quietly handed the served file."""
    root = sandbox_with("pkg/added.py")
    try:
        p, why = coder.readable(root, "added.py", [SERVED])
        check("an un-served basename is refused", p is None, f"got {p!r} (must not be {root / SERVED})")
        check("  and is not resolved to the served file", p != root / SERVED, repr(p))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_path_tricks_are_still_refused():
    """The sandbox boundary is untouched by this change."""
    root = sandbox_with(SERVED)
    try:
        for rel in ("/etc/passwd", "../outside.py", "chat/secrets.py"):
            p, why = coder.readable(root, rel, [SERVED])
            check(f"{rel!r} is refused", p is None, repr(why))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    for t in (test_basename_of_a_served_file_is_served,
              test_repo_file_outside_the_sandbox_is_not_called_absent,
              test_genuinely_absent_path_still_says_no_such_file,
              test_a_basename_that_is_not_served_is_not_served,
              test_path_tricks_are_still_refused):
        print(t.__name__)
        t()
    print(f"\n{len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
