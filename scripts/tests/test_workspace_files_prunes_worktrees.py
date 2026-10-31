#!/usr/bin/env python3
"""2026-10-26 (t4641): a file inside ANOTHER COMMIT'S worktree is not a file this tree has.

The measurement that found it: `check_workspace --quick` says

    OK -- 62 passed, 9 warning(s), 0 failed        on the live checkout ~/ai-workspace
    57 passed, 10 warning(s), 1 failed             on a fresh worktree at the same commit

and the failure is "18 reference(s) resolve to nothing and are NOT declared", every one pointing at
phase0_numbers.py, rerun_phase0.py or session_review_grade_audit.py -- the three one-off audit tools
DELETED in 3869ac66b. Same committed content in both trees. The live checkout passes because
os.walk (scripts/check_workspace.py:1113) reaches .claude/worktrees/*, which are checkouts of OLD
commits that still contain those three files, so the basenames census at :3490 contains names this
tree does not have and the deleted files read as present.

So the green was bought by a stale copy of a deleted file living in a directory that belongs to a
different commit. PRUNED_DIRS never named it. Same shape as the four corrections of 2026-10-25: the
instrument looked where files LIVE rather than where they belong.

Hermetic: a TemporaryDirectory. No git, no model, no ledger.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


class _Repo:
    """workspace_files() reads only self.repo, so the census needs nothing else."""

    def __init__(self, root):
        self.repo = Path(root).resolve()


def names(root):
    return {p.name for p in cw.Checker.workspace_files(_Repo(root))}


def main():
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        (r / "scripts").mkdir()
        (r / "docs").mkdir()
        # A real file of this tree.
        (r / "scripts" / "kept.py").write_text("def f():\n    return 1\n")
        (r / "docs" / "x.md").write_text("# a doc\n")
        # A DELETED file, still present inside another commit's checkout -- exactly the live case.
        (r / ".claude" / "worktrees" / "old-commit" / "scripts").mkdir(parents=True)
        (r / ".claude" / "worktrees" / "old-commit" / "scripts" / "deleted_upstream.py").write_text(
            "def gone():\n    return 0\n")
        # .claude itself may hold files this repository DOES own (settings.json), so the prune must
        # not be "ignore .claude": it names the worktrees directory.
        (r / ".claude" / "settings.json").write_text("{}\n")

        got = names(r)
        check("a file that survives only inside another commit's worktree is not in this tree",
              "deleted_upstream.py" not in got, sorted(got))
        check("  and a file this tree really has is still counted", "kept.py" in got, sorted(got))
        check("  and .claude's own files are not pruned away",
              "settings.json" in got, sorted(got))
        check("  and .git is still pruned", ".git" not in got, sorted(got))

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
