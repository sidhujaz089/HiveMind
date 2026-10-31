"""t1085 -- the deferral-time anchor check.

Why: workspace/rca/a480fe04ce93.md. A seven-file harvest was deferred to Jas as the sentence
"it's yours to make" while the guarded work existed only as uncommitted working-tree diffs in
two sandbox clones (31 of 32 files dirty; the committed logs ended at 6a743e9, before baseline
31f9891). A deferral whose held work is not committed to a named branch is externally
indistinguishable from a step that never happened. This suite pins, as behaviour, the one read
that answers "is the guarded work committed, on a named branch?" It belongs in
scripts/session_review.py, the module that already owns the deferral contract.

Run: python3 scripts/tests/test_deferral_anchor.py     (pytest is not installed)

Red on main for ONE reason: scripts/session_review.py defines no deferral_anchor().
No live state is touched: every repository is created inside a tempfile.TemporaryDirectory; no
model call, no live server, no process left running; every subprocess.run carries timeout=30.
The module under test is pointed at temp paths only -- it is never pointed at this repository.

Do not invent a function, a module, or a file path: the API below is the contract.

    deferral_anchor(cwd, branch=None) -> dict

Exactly five keys: clean, branch, head, ok, detail.
  clean  True only when `git -C <cwd> status --porcelain` prints nothing. Default
         untracked-files mode, so a brand-new untracked file is NOT clean.
  branch stdout of `git -C <cwd> symbolic-ref --short HEAD`, stripped; '' when that exits
         non-zero (detached HEAD).
  head   stdout of `git -C <cwd> rev-parse HEAD`, stripped; '' when that exits non-zero
         (a repo with no commits -- measured: exit 128).
  ok     clean AND branch != '' AND head != '' AND (branch is None OR branch == branch arg).
  detail a single human sentence, never truncated, naming the reason: the porcelain output
         VERBATIM when dirty (so the reader sees which paths), the word 'detached' when
         branch is '', the phrase 'no commit' when head is '', and BOTH the expected and the
         actual branch names on a mismatch. For a directory that is not a repository it
         passes through git's own wording, which contains 'not a git repository'.
Every subprocess.run carries timeout=30. A missing git binary (FileNotFoundError) and a
killed check (subprocess.TimeoutExpired) are recorded INTO detail with ok=False; never
re-raised, never swallowed, and the function never returns None.
"""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr

GIT = shutil.which("git")
GIT_ID = ("-c", "user.name=oracle", "-c", "user.email=oracle@example.invalid",
          "-c", "commit.gpgsign=false")

if not hasattr(sr, "deferral_anchor"):
    print("FATAL: scripts/session_review.py defines no deferral_anchor() -- that is the "
          "function this suite exists to build", file=sys.stderr)
    sys.exit(1)


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, timeout=30)


def init_repo(cwd, branch="main"):
    r = git(cwd, "-c", "init.defaultBranch=" + branch, *GIT_ID, "init", "-q")
    assert r.returncode == 0, r.stderr
    return r


def commit_file(cwd, name, content, msg):
    (Path(cwd) / name).write_text(content)
    add = git(cwd, *GIT_ID, "add", "-A")
    assert add.returncode == 0, add.stderr
    c = git(cwd, *GIT_ID, "commit", "-q", "-m", msg)
    assert c.returncode == 0, c.stderr
    return c


def porcelain(cwd):
    r = git(cwd, "status", "--porcelain")
    assert r.returncode == 0, r.stderr
    return r.stdout


class DeferralAnchorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        init_repo(self.repo)

    def test_clean_tree_on_named_branch_is_ok(self):
        commit_file(self.repo, "harvest.py", "print(1)\n", "harvest work")
        r = sr.deferral_anchor(self.repo)
        self.assertEqual({"clean", "branch", "head", "ok", "detail"}, set(r),
                         "the deferral row's keys are pinned by t1085")
        self.assertIs(True, r["clean"])
        self.assertIs(True, r["ok"])
        self.assertEqual("main", r["branch"])
        self.assertEqual(40, len(r["head"]))
        self.assertTrue(all(c in "0123456789abcdef" for c in r["head"]), r["head"])
        self.assertTrue(r["detail"].strip(), "the result must state what it found")
        # reading must not change the repository it inspects
        self.assertEqual("", porcelain(self.repo))

    def test_uncommitted_modification_is_not_a_durable_handle(self):
        commit_file(self.repo, "harvest.py", "print(1)\n", "harvest work")
        (self.repo / "harvest.py").write_text("print(2)\n")
        r = sr.deferral_anchor(self.repo)
        self.assertIs(False, r["ok"])
        self.assertIs(False, r["clean"])
        self.assertIn("harvest.py", r["detail"])
        # negative assertion: the check reports dirt, it never cleans the tree it reports on
        self.assertIn("harvest.py", porcelain(self.repo))

    def test_untracked_harvest_file_is_not_clean(self):
        # The case a `git diff --quiet` implementation passes vacuously. The harvest that
        # was deferred in rca a480fe04ce93 was new files, not edits.
        commit_file(self.repo, "harvest.py", "print(1)\n", "harvest work")
        (self.repo / "notes.md").write_text("handover notes\n")
        r = sr.deferral_anchor(self.repo)
        self.assertIs(False, r["clean"])
        self.assertIs(False, r["ok"])
        self.assertIn("notes.md", r["detail"])

    def test_detached_head_is_not_a_named_branch(self):
        commit_file(self.repo, "first.py", "1\n", "one")
        commit_file(self.repo, "second.py", "2\n", "two")
        out = git(self.repo, *GIT_ID, "checkout", "-q", "HEAD~1")
        self.assertEqual(0, out.returncode, out.stderr)
        r = sr.deferral_anchor(self.repo)
        self.assertEqual("", r["branch"])
        self.assertIs(False, r["ok"])
        self.assertIn("detached", r["detail"].lower())

    def test_repo_with_no_commits_is_not_ok(self):
        # `git init` alone: status is clean and symbolic-ref prints the branch name, so a
        # check that reads the branch name alone calls an empty repository a named branch.
        # Measured: `git rev-parse HEAD` exits 128 there.
        r = sr.deferral_anchor(self.repo)
        self.assertIs(False, r["ok"])
        self.assertEqual("", r["head"])
        self.assertIn("no commit", r["detail"].lower())

    def test_the_named_branch_must_be_the_one_checked_out(self):
        commit_file(self.repo, "harvest.py", "print(1)\n", "harvest work")
        r = sr.deferral_anchor(self.repo, branch="feature/harvest")
        self.assertIs(False, r["ok"])
        self.assertIn("feature/harvest", r["detail"])
        self.assertIn("main", r["detail"])

    def test_the_requested_branch_is_accepted_when_it_is_checked_out(self):
        commit_file(self.repo, "harvest.py", "print(1)\n", "harvest work")
        out = git(self.repo, *GIT_ID, "checkout", "-q", "-b", "feature/harvest")
        self.assertEqual(0, out.returncode, out.stderr)
        r = sr.deferral_anchor(self.repo, branch="feature/harvest")
        self.assertIs(True, r["ok"])
        self.assertEqual("feature/harvest", r["branch"])

    def test_a_directory_that_is_not_a_repository_is_reported_not_raised(self):
        plain = Path(self._tmp.name) / "not-a-repo"
        plain.mkdir()
        r = sr.deferral_anchor(plain)
        self.assertIs(False, r["ok"])
        self.assertTrue(r["detail"].strip())
        self.assertIn("not a git", r["detail"].lower())

    def test_a_path_that_does_not_exist_is_reported_not_raised(self):
        r = sr.deferral_anchor(Path(self._tmp.name) / "no-such-directory")
        self.assertIs(False, r["ok"])
        self.assertTrue(r["detail"].strip())
        self.assertIn("no such file", r["detail"].lower())


def main():
    if GIT is None:
        print("FATAL: git is not on PATH; these checks need real git repositories",
              file=sys.stderr)
        return 1
    suite = unittest.TestLoader().loadTestsFromTestCase(DeferralAnchorTests)
    res = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if res.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
