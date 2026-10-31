#!/usr/bin/env python3
"""A branch is "ready to merge" only if main does not already have its work.

RCA 2026-10-28 (Jas): "Claude had 10 items to merge. There really weren't." Ten branches that were fully
merged were shown under "Nothing is merged -- that is your call", because the page tested whether the REF
still existed (reviewq.branch_exists) and nothing deletes a branch after a merge. Every one was an
ancestor of main; `git cherry` showed no commits left on any of them.

This suite builds a real repository -- merged branch, unmerged branch, missing branch -- and pins that
the question is asked of git, that a branch git cannot judge is NOT called merged, and that the page's
predicate picks only real work.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def main():
    import reviewq as R
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    d = Path(tempfile.mkdtemp()) / "repo"
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    (d / "a.txt").write_text("base\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "base")
    # a branch that is merged into main, and its ref left behind -- the ten cards
    git(d, "checkout", "-qb", "coder/merged")
    (d / "b.txt").write_text("done\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "merged work")
    git(d, "checkout", "-q", "main")
    git(d, "merge", "-q", "--no-ff", "-m", "merge coder/merged", "coder/merged")
    # a branch with real work main does not have
    git(d, "checkout", "-qb", "coder/unmerged")
    (d / "c.txt").write_text("pending\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "unmerged work")
    git(d, "checkout", "-q", "main")

    real = R.REPO
    try:
        R.REPO = d
        check("a MERGED branch whose ref still exists is NOT unmerged work",
              R.branch_merged("coder/merged") == (True, False), str(R.branch_merged("coder/merged")))
        check("a branch main does not have IS unmerged work",
              R.branch_merged("coder/unmerged") == (True, True), str(R.branch_merged("coder/unmerged")))
        check("a name that is not a branch is neither",
              R.branch_merged("coder/never-existed") == (False, False),
              str(R.branch_merged("coder/never-existed")))
        check("  empty name is not a fault", R.branch_merged("") == (False, False))

        # THE PAGE'S PREDICATE: only branches with unmerged work are cards.
        judged = {b: R.branch_merged(b) for b in ("coder/merged", "coder/unmerged", "coder/never-existed")}
        live = [b for b, (exists, unmerged) in judged.items() if exists and unmerged]
        already = [b for b, (exists, unmerged) in judged.items() if exists and unmerged is False]
        check("the page shows only the unmerged one", live == ["coder/unmerged"], str(live))
        check("  and names the merged refs for cleanup instead of calling them ready",
              already == ["coder/merged"], str(already))
    finally:
        R.REPO = real

    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    check("the page asks git, not branch_exists, for this list",
          "rq.branch_merged(" in src and "live = [v for v, (exists, unmerged) in judged if exists and unmerged]"
          in src)
    check("  and dedupes by BRANCH, so an older unmerged attempt is not hidden",
          "newest verdict per BRANCH wins" in src)
    print("test_ready_to_merge_means_unmerged:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
