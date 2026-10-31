#!/usr/bin/env python3
"""FAIL-FIRST oracle for scope_lint.dirty_leaks_to_tracked_dir + its wiring into
run_oracles (t699 / t659 oracle-dirty).

Proves, with no model and no live repo (a throwaway git repo in a temp dir), that:
  (a) the pure classifier dirty_leaks_to_tracked_dir(dirty_line, tracked_paths)
      returns True only for a NEW untracked file ('??') that lands in a directory
      that already contains a tracked file, and False for a modified tracked file
      or a new file in an untracked-only dir;
  (b) run_oracles() EMITS the finding 'dirty-leaks-to-tracked-dir' for an oracle
      that writes a new file into a tracked data dir, and does NOT emit it for a
      hermetic (tempfile-only) oracle -- i.e. the check is wired, not a dead path.

Red on main because dirty_leaks_to_tracked_dir does not exist (AttributeError in
(a)) and run_oracles does not emit the new class yet ((b)). Only stdlib + git.
Every subprocess has a timeout. No fixed /tmp paths.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import scope_lint  # noqa: E402

FAILED = []


def check(name, cond):
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        FAILED.append(name)


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, timeout=30)


def _mk_fake_repo():
    """A throwaway git repo whose main has a TRACKED file data/keep.json, so a NEW
    file written into data/ is 'a new untracked file in a tracked dir'."""
    root = Path(tempfile.mkdtemp(prefix="dirty-leak-"))
    _git(root, "init", "-q")
    _git(root, "branch", "-M", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "data").mkdir()
    (root / "data" / "keep.json").write_text("{}\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    return root


def _class_of(findings, cls):
    return [t for t in findings if t and t[0] == cls]


def _evidence(findings, cls):
    return [t[3] for t in findings if t and t[0] == cls]


def test_classifier():
    f = scope_lint.dirty_leaks_to_tracked_dir
    check("new untracked file in a tracked dir is a leak",
          f("?? data/leaked.json", {"data/keep.json"}) is True)
    check("nested new file in a tracked dir is a leak",
          f("?? a/b/c/leak.json", {"a/b/c/keep.json"}) is True)
    check("new file in an untracked-only dir is not a leak",
          f("?? tmp/leak.json", {"data/keep.json"}) is False)
    check("new file with no tracked files at all is not a leak",
          f("?? data/leak.json", set()) is False)
    check("a modified tracked file is not classified as a new-file leak",
          f(" M data/keep.json", {"data/keep.json"}) is False)


def test_wiring():
    repo = _mk_fake_repo()
    try:
        dirty_oracle = "from pathlib import Path\nPath('data/leaked.json').write_text('x\\n')\n"
        leak = scope_lint.run_oracles(
            [{"kind": "oracle", "file": "data/leaked_oracle.py",
              "oracle_text": dirty_oracle}], repo, ref="main")
        check("run_oracles still flags the dirt as oracle-dirty",
              bool(_class_of(leak, "oracle-dirty")))
        check("run_oracles emits the new dirty-leaks-to-tracked-dir finding",
              bool(_class_of(leak, "dirty-leaks-to-tracked-dir")))
        check("the leak finding names the leaking path",
              any("data/leaked.json" in e for e in _evidence(leak, "dirty-leaks-to-tracked-dir")))

        hermetic = ("import tempfile, os\n"
                    "d = tempfile.mkdtemp(); open(os.path.join(d, 'x'), 'w').write('x')\n")
        clean = scope_lint.run_oracles(
            [{"kind": "oracle", "file": "clean_oracle.py",
              "oracle_text": hermetic}], repo, ref="main")
        check("a hermetic oracle is NOT flagged as a tracked-dir leak",
              not _class_of(clean, "dirty-leaks-to-tracked-dir"))
        check("a hermetic oracle is NOT flagged as dirty",
              not _class_of(clean, "oracle-dirty"))
    finally:
        try:
            _git(repo, "worktree", "prune")
        except Exception:
            pass
        shutil.rmtree(repo, ignore_errors=True)


def run():
    test_classifier()
    test_wiring()
    if FAILED:
        print("FAIL  %d assertion(s): %s" % (len(FAILED), FAILED))
        return 1
    print("PASS  all assertions")
    return 0


if __name__ == "__main__":
    sys.exit(run())
