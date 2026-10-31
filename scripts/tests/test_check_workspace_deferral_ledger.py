"""t1275 -- check_workspace FAILS while the production deferral ledger is untracked.

RCA workspace/rca/e6e6a8cfb5f9.md, item 4: the record built to stop deferrals being lost is
itself the kind of record that gets lost. As verified on 2026-10-16 and again on 2026-10-17,
`git ls-files --error-unmatch workspace/deferrals.jsonl` exits 1 and `git check-ignore -v`
exits 1: the ledger is neither tracked nor ignored, and no standing check said so, so the
state persisted from the RCA's timestamp to today. scripts/check_workspace.py is the surface
that is guaranteed to run (session_end.py calls it before a session may finish), so it is
where the ratchet belongs; scripts/tests/test_deferrals_ledger_is_clean.py is the sibling that
checks the ledger's CONTENTS.

RED ON MAIN FOR ONE REASON: scripts/check_workspace.py has no function named
check_deferral_ledger_tracked -- the module has no attribute by that name today. It greens on
adding that check (the method, its entry in Checker.run's list, and the module-level alias
next to check_task_closure_dod at scripts/check_workspace.py:2612).

READ-ONLY against the repository. Every repo it builds lives inside tempfile.mkdtemp(); the
only git commands run are inside those temp dirs. No model call, no live server, no fixed /tmp
path, no process left running; every subprocess carries timeout=.
Run: python3 scripts/tests/test_check_workspace_deferral_ledger.py
"""
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # scripts/tests -> repo root
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace                             # noqa: E402

LEDGER = "workspace/deferrals.jsonl"
ROW = json.dumps({"session": "745a17ba", "deferral": "the fixture rows",
                  "at": "2026-10-16T12:02:00Z"})


def _repo(tmp):
    """A real, throwaway git repo: one commit, and an UNTRACKED ledger on disk.

    A real repo, not a stub: the check's contract is the answer `git ls-files` gives, and a
    fake git object would test the fake.
    """
    d = Path(tmp)
    (d / "workspace").mkdir()
    (d / "workspace" / "deferrals.jsonl").write_text(ROW + "\n", encoding="utf-8")
    (d / "README.md").write_text("fixture\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "README.md"],
                 ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "seed"]):
        p = subprocess.run(["git", "-C", str(d), *args], capture_output=True,
                           text=True, timeout=60)
        if p.returncode != 0:
            raise AssertionError("git %s failed: %s%s" % (args, p.stdout, p.stderr))
    return d


def _verdict(checker):
    """The lines the check printed. self.result() prints and that is its only surface
    (scripts/check_workspace.py:326-329): a check that prints nothing checked nothing."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        check_workspace.check_deferral_ledger_tracked(checker)
    return buf.getvalue()


class CheckWorkspaceDeferralLedgerTracked(unittest.TestCase):

    def test_the_check_exists_on_the_module(self):
        """RED TODAY: no such attribute on scripts/check_workspace.py. The module-level alias
        is part of the contract, not decoration -- see the comment at :2609-2611: a check that
        lives only as a method reads, to a test, as a check that was never built."""
        self.assertTrue(hasattr(check_workspace, "check_deferral_ledger_tracked"),
                        "scripts/check_workspace.py defines no check_deferral_ledger_tracked, "
                        "so the deferral ledger's tracked-ness is checked by nothing at session "
                        "end (workspace/rca/e6e6a8cfb5f9.md item 4)")

    def test_an_untracked_ledger_fails(self):
        """The defect, asserted as a verdict -- the state of this repository today."""
        with tempfile.TemporaryDirectory() as tmp:
            checker = check_workspace.Checker(_repo(tmp), quick=True)
            out = _verdict(checker)
        self.assertIn("FAIL", out, "an untracked workspace/deferrals.jsonl did not FAIL: %r" % out)
        self.assertNotIn("PASS", out, "a verdict that says PASS for an untracked ledger is a "
                                     "green light on the exact gap the RCA names: %r" % out)
        self.assertIn(LEDGER, out, "the FAIL must name the path it is about -- a failure nobody "
                                   "can act on is a failure that gets routed around: %r" % out)

    def test_tracking_the_ledger_passes(self):
        """The green condition, so the check is satisfiable and not an unmovable red. The prior
        scope's brief records the same command for Claude: git add workspace/deferrals.jsonl."""
        with tempfile.TemporaryDirectory() as tmp:
            checker = check_workspace.Checker(_repo(tmp), quick=True)
            add = subprocess.run(["git", "-C", str(tmp), "add", LEDGER],
                                 capture_output=True, text=True, timeout=60)
            self.assertEqual(add.returncode, 0, add.stdout + add.stderr)
            out = _verdict(checker)
        self.assertIn("PASS", out, "the ledger IS tracked in the index and the check did not "
                                  "say PASS: %r" % out)
        self.assertNotIn("FAIL", out, "a check that FAILs once the record is tracked is a check "
                                      "that will be disabled within a week: %r" % out)

    def test_an_ignored_ledger_does_not_pass(self):
        """The INVERSION, and the reason this check may not be written as a scan for untracked
        files: a path named in .gitignore does not appear in `git ls-files --others
        --exclude-standard`, so that shape reports a clean pass for a ledger that has no
        history. Ignoring a record is not tracking it (RCA: untracked AND not gitignored)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = _repo(tmp)
            (d / ".gitignore").write_text(LEDGER + "\n", encoding="utf-8")
            checker = check_workspace.Checker(d, quick=True)
            out = _verdict(checker)
        self.assertIn("FAIL", out, "a .gitignore entry was accepted as tracking: the ledger is "
                                   "still absent from history, which is the defect: %r" % out)
        self.assertNotIn("PASS", out, "PASS for an ignored ledger is the false green this "
                                      "check exists to prevent: %r" % out)

    def test_a_missing_ledger_does_not_pass(self):
        """A ledger that is not on disk is not a clean ledger. Reported, never skipped past --
        the standard this file sets for itself at scripts/check_workspace.py:485-516."""
        with tempfile.TemporaryDirectory() as tmp:
            d = _repo(tmp)
            (d / LEDGER).unlink()
            checker = check_workspace.Checker(d, quick=True)
            out = _verdict(checker)
        self.assertIn("FAIL", out, "a missing workspace/deferrals.jsonl did not FAIL: %r" % out)
        self.assertNotIn("PASS", out, "absence is not cleanliness: %r" % out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
