#!/usr/bin/env python3
"""The worktree reaper removes copies of commits main has, and keeps anything that is somebody's work (t9566).

206 worktrees had piled up under ~/.cache/hivemind-tmp (2026-10-29), 99 of the merged ones "dirty" only because
services append to ledgers inside them. A throwaway repo checks each case the reaper must get right.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import sweep  # noqa: E402

TMP = Path.home() / ".cache/hivemind-tmp/test-worktree-reaper"
FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    FAILED += not ok
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))


def g(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)


shutil.rmtree(TMP, ignore_errors=True)
repo, wts = TMP / "repo", TMP / "wts"
(repo / "workspace").mkdir(parents=True)
wts.mkdir()
g("init", "-q", "-b", "main", cwd=repo)
g("config", "user.email", "t@t", cwd=repo); g("config", "user.name", "t", cwd=repo)
(repo / "a.py").write_text("x = 1\n")
(repo / "workspace" / "runs.jsonl").write_text("{}\n")
g("add", "-A", cwd=repo); g("commit", "-qm", "base", cwd=repo)
g("branch", "unmerged", cwd=repo); g("checkout", "-q", "unmerged", cwd=repo)
(repo / "a.py").write_text("x = 2\n"); g("commit", "-qam", "u", cwd=repo); g("checkout", "-q", "main", cwd=repo)


def wt(name, ref="main"):
    p = wts / name
    g("worktree", "add", "-q", "--detach", str(p), ref, cwd=repo)
    return p


clean = wt("clean")
ledger = wt("ledger-only"); (ledger / "workspace" / "runs.jsonl").write_text("{}\n{}\n")
edit = wt("real-edit"); (edit / "a.py").write_text("x = 99\n")
newf = wt("untracked-file"); (newf / "new_tool.py").write_text("print(1)\n")
unm = wt("unmerged-ledger", "unmerged"); (unm / "workspace" / "runs.jsonl").write_text("{}\n{}\n")
scratch = wt("merge-reviewed")

sweep.REPO = repo
rows = sweep.sweep_tmp_worktrees(str(wts), max_age_s=0, holders=[], ledger=TMP / "ledger.jsonl")
check("a clean worktree on main's commit is removed", not clean.exists())
check("a merged worktree dirty ONLY in runtime ledgers is removed", not ledger.exists())
check("a worktree with a real edit is KEPT", edit.exists())
check("a worktree with an untracked new file is KEPT", newf.exists())
check("an UNMERGED worktree is kept even if only ledgers changed", unm.exists())
check("the merge path's scratch is never reaped", scratch.exists())
check("what it removed is recorded", len(rows) == 2, rows)

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAILED else 0)
