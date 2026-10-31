#!/usr/bin/env python3
"""Phase 0a: the live checkout refuses switch / detach / stash, and lets the line's own git through.

A FIXTURE REPOSITORY, NEVER THIS ONE. It gets copies of the shipped reference-transaction and
post-checkout hooks and live_repo_guard.py, and HIVEMIND_LIVE_REPO points at it, so the fixture IS
the live checkout as far as the guard knows. role_gate.py is deliberately not copied: its own suite
covers it, and here it would only add its refusals to ours.

ALLOWED is the list of git operations the line runs against ~/ai-workspace today, read from the code
on 2026-10-27: auto_merge.py (worktree add --detach, merge --no-ff, merge --abort, branch -d,
worktree remove --force), merge_gate.py (worktree add --detach <branch>, merge --no-commit inside it),
session_end.py (commit, branch --list, rev-parse), script_audit.py (checkout -b INSIDE a worktree),
and `merge --ff-only` into main. A hook that refuses any of these is an outage.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PASSED = FAILED = 0


def report(name, ok, detail=""):
    global PASSED, FAILED
    PASSED += bool(ok)
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        {detail}"))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="live-guard-"))
    live = tmp / "live"
    env = {**os.environ, "HIVEMIND_LIVE_REPO": str(live), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    env.pop("HIVEMIND_LIVE_GIT_OK", None)

    def git(*a, cwd=live, extra=None):
        p = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, env={**env, **(extra or {})})
        return p.returncode, p.stdout + p.stderr

    try:
        live.mkdir()
        git("init", "-q", "-b", "main")
        (live / "scripts" / "hooks").mkdir(parents=True)
        for h in ("reference-transaction", "post-checkout", "live_repo_guard.py"):
            shutil.copy2(REPO / "scripts" / "hooks" / h, live / "scripts" / "hooks" / h)
        git("config", "core.hooksPath", "scripts/hooks")
        (live / "f").write_text("a\n")
        git("add", "-A")
        rc, out = git("commit", "-qm", "base")
        report("commit on main is allowed", rc == 0, out)

        # ---- what the line does: all must pass
        git("branch", "feature")
        wt = tmp / "wt"
        rc, out = git("worktree", "add", "-q", "--detach", str(wt), "feature")
        report("worktree add --detach (auto_merge, merge_gate)", rc == 0, out)
        (wt / "g").write_text("g\n")
        git("add", "g", cwd=wt)
        rc, out = git("checkout", "-q", "-b", "feature2", cwd=wt)
        report("checkout -b INSIDE a worktree (script_audit)", rc == 0, out)
        rc, out = git("commit", "-qm", "g", cwd=wt)
        report("commit inside a worktree", rc == 0, out)
        rc, out = git("merge", "-q", "--no-commit", "--no-ff", "main", cwd=wt)
        report("merge --no-commit inside a worktree (merge_gate)", rc == 0, out)
        git("merge", "--abort", cwd=wt)
        rc, out = git("worktree", "remove", "--force", str(wt))
        report("worktree remove --force", rc == 0, out)
        rc, out = git("worktree", "add", "-q", str(wt), "-b", "lane/x", "main")
        report("worktree add -b <branch> main (how lanes work)", rc == 0, out)
        git("worktree", "remove", "--force", str(wt))
        rc, out = git("merge", "-q", "--no-ff", "-m", "Merge feature2", "feature2")
        report("merge --no-ff into main (auto_merge)", rc == 0, out)
        git("branch", "ff", "feature2")
        (live / "h").write_text("h\n")
        git("add", "h")
        git("commit", "-qm", "h")
        git("branch", "-f", "ff", "main")
        wt2 = tmp / "wt2"
        git("worktree", "add", "-q", str(wt2), "ff")
        (wt2 / "i").write_text("i\n")
        git("add", "i", cwd=wt2)
        git("commit", "-qm", "i", cwd=wt2)
        git("worktree", "remove", "--force", str(wt2))
        rc, out = git("merge", "-q", "--ff-only", "ff")
        report("merge --ff-only into main", rc == 0, out)
        rc, out = git("branch", "-d", "ff")
        report("branch -d (auto_merge)", rc == 0, out)
        (live / "j").write_text("x\n")
        git("add", "j")
        git("branch", "conflict", "HEAD~1")
        rc, out = git("merge", "--abort")   # nothing to abort: must fail for git's reason only
        report("merge --abort is not refused by the guard", "live_repo_guard" not in out, out)
        git("reset", "-q")
        (live / "j").unlink()

        # ---- the same, the way the scripts call it: `git -C <live> ...` from elsewhere (L4, 05:34Z:
        # auto_merge, bridge_trigger, scope_lint and merge_gate all do this, and the first draft read
        # the -C PATH as the subcommand and refused every one of them)
        wt3 = tmp / "wt3"
        rc, out = git("-C", str(live), "worktree", "add", "-q", "--detach", str(wt3), "main", cwd=tmp)
        report("git -C <live> worktree add --detach (auto_merge, merge_gate)", rc == 0, out)
        git("-C", str(live), "worktree", "remove", "--force", str(wt3), cwd=tmp)
        rc, out = git("-C", str(live), "-c", "core.quotePath=false", "worktree", "add", "-q", str(wt3),
                      "-b", "lane/y", "main", cwd=tmp)
        report("git -C <live> -c k=v worktree add -b (flags with values skipped)", rc == 0, out)
        git("-C", str(live), "worktree", "remove", "--force", str(wt3), cwd=tmp)
        rc, out = git("-C", str(live), "switch", "-q", "feature", cwd=tmp)
        report("git -C <live> switch is still REFUSED", rc != 0 and "REFUSED" in out, out)

        # ---- what must be refused
        (live / "only-on-main").write_text("m\n")
        git("add", "only-on-main")
        git("commit", "-qm", "only on main")
        rc, out = git("switch", "-q", "feature")
        report("git switch <branch> is REFUSED", rc != 0 and "live_repo_guard: REFUSED" in out, out)
        _, st = git("status", "--porcelain", "--untracked-files=no")
        report("  and the refused switch left the tree and index as they were (L3, 13:19Z)",
               st.strip() == "" and (live / "only-on-main").exists(), st)
        (live / "f").write_text("a service's uncommitted row\n")
        rc, out = git("switch", "-q", "feature")
        report("a service's uncommitted write survives a refused switch",
               rc != 0 and (live / "f").read_text() == "a service's uncommitted row\n",
               (live / "f").read_text())
        git("checkout", "--", "f")
        rc, out = git("checkout", "-q", "feature")
        report("git checkout <branch> is REFUSED", rc != 0 and "REFUSED" in out, out)
        rc, out = git("checkout", "-q", "-b", "dsh/oops")
        report("git checkout -b in the live repo is REFUSED", rc != 0 and "REFUSED" in out, out)
        rc, out = git("checkout", "-q", "--detach", "HEAD")
        report("detaching HEAD is REFUSED", rc != 0 and "REFUSED" in out, out)
        rc, _ = git("symbolic-ref", "HEAD")
        _, head = git("symbolic-ref", "HEAD")
        report("HEAD still on main after the refusals", head.strip() == "refs/heads/main", head)
        (live / "f").write_text("dirty\n")
        rc, out = git("stash", "-q")
        report("git stash is REFUSED", rc != 0 and "REFUSED" in out, out)
        report("the dirty file survived the refused stash", (live / "f").read_text() == "dirty\n")
        rc, out = git("checkout", "--", "f")
        report("path checkout is WARNED about (cannot be refused)", "WARNING" in out, out)
        rc, out = git("switch", "-q", "feature", extra={"HIVEMIND_LIVE_GIT_OK": "1"})
        report("HIVEMIND_LIVE_GIT_OK=1 lets one switch through, loudly", rc == 0 and "ALLOWED" in out, out)
        rc, out = git("switch", "-q", "main", extra={"HIVEMIND_LIVE_GIT_OK": "1"})

        # ---- a clone is not live
        clone = tmp / "clone"
        subprocess.run(["git", "clone", "-q", str(live), str(clone)], env=env)
        git("config", "core.hooksPath", "scripts/hooks", cwd=clone)
        rc, out = git("switch", "-q", "-c", "other", cwd=clone)
        report("a switch in a CLONE is allowed (not the live checkout)", rc == 0, out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
