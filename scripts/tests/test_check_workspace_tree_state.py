#!/usr/bin/env python3
"""t622: check_workspace.py must RECORD the working-tree state it asserts.

The finding this pins (workspace/rca/1fec19bbc2cd.md, task t622): a start-of-session
gate printed 'Working tree is clean', that verdict was then treated as standing truth,
and the tree changed 11 minutes later with nothing recording the delta. The class is a
state gate with no recorder, so a 'clean' is a claim rather than a fact.

This is the decay test the RCA names: it is green ONLY if the writer is in force.
It asserts BEHAVIOUR -- a row whose count, verdict, file list and head sha each match
an independent 'git status --porcelain' / 'git rev-parse HEAD' the oracle runs itself.
A clean tree must be recorded as clean (a row saying zero), not merely leave no row.

    python3 scripts/tests/test_check_workspace_tree_state.py

pytest is not installed: the __main__ block runs every test and exits non-zero on any
failure. Temp dirs and fakes only -- nothing here touches the repository it tests.
"""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from check_workspace import Checker  # noqa: E402

PASSED = 0
FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, timeout=60)


def make_repo():
    """A minimal repo shaped like the real one, fully committed -- so its tree is CLEAN.

    Copied from scripts/tests/test_check_workspace.py:45-74 so this suite exercises the
    same fixture the existing one does, not a fixture I imagined.
    """
    root = Path(tempfile.mkdtemp())
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")

    (root / "STATE.md").write_text("# STATE\n\n**Last updated:** 2030-01-01\n")
    (root / "workspace").mkdir()
    for box in ("inbox", "outbox", "feedback"):
        d = root / "workspace" / box
        d.mkdir()
        (d / "README.md").write_text(f"# {box}\n")
    (root / "workspace" / "sessions" / "chat").mkdir(parents=True)
    (root / "workspace" / "sessions" / "chat" / "README.md").write_text("# chat\n")
    (root / "workspace" / "PROJECT-INDEX.md").write_text("# index\n")
    (root / "projects" / "demo").mkdir(parents=True)
    (root / "projects" / "demo" / "STATUS.md").write_text("# demo\n")

    hooks = root / "scripts" / "hooks"
    hooks.mkdir(parents=True)
    for h in ("pre-commit", "commit-msg", "pre-push"):
        p = hooks / h
        p.write_text("#!/bin/sh\nexit 0\n")
        p.chmod(0o755)

    git(root, "add", "-A")
    git(root, "-c", "core.hooksPath=", "commit", "-q", "-m", "init", "--no-verify")
    git(root, "config", "core.hooksPath", "scripts/hooks")
    return root


def run_git_state(root):
    """Run the real check_git_state on a temp repo, quietly.

    check_git_state() is called directly, NOT run(): run() ends with _record_history(),
    whose HISTORY path is script-relative (check_workspace.py:2356) and would append
    fixture rows into the repository under test. The writer under test must be
    repo-relative, so this exercises it without writing outside tempdir.
    """
    c = Checker(root, quick=True)
    c.result = lambda s, n, d=None: None
    c.section = lambda t: None
    c.check_git_state()


def rows(root):
    led = root / "workspace" / "ledgers" / "tree-state.jsonl"
    if not led.is_file():
        return []
    out = []
    for line in led.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def porcelain_files(root):
    """Ground truth, measured by this suite -- not taken from the code under test."""
    p = git(root, "status", "--porcelain")
    return sorted(l[3:] for l in (p.stdout or "").splitlines() if l.strip())


def test_dirty_tree_is_recorded_by_name():
    r = make_repo()
    (r / "STATE.md").write_text("# STATE\n\n**Last updated:** 2030-01-02\n")
    (r / "dirty.md").write_text("untracked and dirty\n")
    want = porcelain_files(r)
    check("the fixture really is dirty (>=2 files)", len(want) >= 2, True)

    before = len(rows(r))
    run_git_state(r)
    after = rows(r)
    check("a check run appends exactly one tree-state row", len(after) - before, 1)
    row = after[-1] if len(after) > before else {}

    check("the row's dirty_count equals a concurrent 'git status --porcelain' count",
          row.get("dirty_count"), len(want))
    check("a dirty tree's verdict is the verdict the check prints (WARN)",
          row.get("verdict"), "WARN")
    check("the row names the dirty files, not only a count", sorted(row.get("files", [])), want)
    check("the row is stamped with this commit's head sha",
          row.get("git_head_sha"), git(r, "rev-parse", "HEAD").stdout.strip())
    ts = row.get("ts_utc", "")
    check("the row carries a UTC timestamp",
          (len(ts) == 20 and ts.endswith("Z")), True)
    check("a dirty tree's row does not claim the tree is clean",
          row.get("dirty_count", 0) > 0, True)
    shutil.rmtree(r, ignore_errors=True)


def test_clean_tree_is_recorded_as_clean():
    """The negative half: 'clean' must be a recorded fact, not the absence of a warning."""
    r = make_repo()
    check("the fixture tree is clean", porcelain_files(r), [])

    before = len(rows(r))
    run_git_state(r)
    after = rows(r)
    check("a clean tree also gets a row", len(after) - before, 1)
    row = after[-1] if len(after) > before else {}

    check("a clean tree's verdict is the check's PASS", row.get("verdict"), "PASS")
    check("a clean tree's row records zero dirty files", row.get("dirty_count"), 0)
    check("a clean tree's row carries an empty file list, not a missing key",
          row.get("files"), [])
    shutil.rmtree(r, ignore_errors=True)


def test_ledger_appends_rather_than_replacing():
    r = make_repo()
    (r / "STATE.md").write_text("first change\n")
    run_git_state(r)
    first = len(rows(r))
    (r / "STATE.md").write_text("second change\n")
    run_git_state(r)
    after = rows(r)
    check("each run appends; the earlier row is kept", len(after), first + 1)
    check("both rows carry a file list (a record over time, not a cache)",
          len([x for x in after if isinstance(x.get("files"), list)]), 2)
    shutil.rmtree(r, ignore_errors=True)


if __name__ == "__main__":
    print("\ncheck_workspace.py tree-state ledger (t622)\n")
    test_dirty_tree_is_recorded_by_name()
    test_clean_tree_is_recorded_as_clean()
    test_ledger_appends_rather_than_replacing()
    print(f"\ntree-state ledger: {PASSED} passed, {FAILED} failed")
    sys.exit(1 if FAILED else 0)
