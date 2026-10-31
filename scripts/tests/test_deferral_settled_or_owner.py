"""t1351 item 1 -- check_workspace FAILS while workspace/deferrals.jsonl holds a row that
carries neither a settled_by recorder command nor an owner.

The RCA behind this task (workspace/rca/13773fe318de.md, quoted in the task file
t1351--require-settled-by-or-owner-on-every-flagged-num.md) names the evaporation: a flagged
size/fit/memory number that advances the session with no recorder command and no owner leaves
a thread with no row that can fail, and the number -- there, 1.05 GB against nvidia-smi's
21014 MiB used -- is uncheckable afterwards.

The rows live in workspace/deferrals.jsonl. Verified 2026-10-17 by reading the live file:
2297 rows; every row carries exactly the keys item, reason, closer, session, time; ZERO rows
carry settled_by and ZERO carry owner; 2205 carry the writer's own closer "UNTRACKED:
session-review deferral with no owner recorded". The ledger records in its own data that
nobody owns the deferral, and no check reads row content: the 28 checks in
scripts/check_workspace.py are methods of class Checker (lines 375 to 2463) and the word
deferral reaches that file only at check_deferrals, check_deferral_numbers and the
registration list in Checker.run.

RED ON MAIN FOR ONE REASON, not for its own bug: scripts/check_workspace.py defines no
check_deferral_settled_or_owner, so the first test's hasattr is False and the six behaviour
tests raise AttributeError inside the verdict helper. It greens on adding that method to
class Checker, registering it in Checker.run's list beside check_deferral_numbers, and
binding the module-level alias at the foot of the file next to the alias of
check_task_closure_dod.

A fixture note: Checker.__init__ runs git rev-parse under timeout=10 and records a note when
git declines (its own comment says the fallback is deliberate), so a bare temp directory is a
legal repo_root for one check -- no git, no subprocess, no write outside the temp dir, no
model call, no live state. This file starts no process and takes no lease.
Run: python3 scripts/tests/test_deferral_settled_or_owner.py
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # scripts/tests -> repo root
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace                             # noqa: E402

LEDGER = "workspace/deferrals.jsonl"

# The row shape is the LIVE one, read off workspace/deferrals.jsonl on 2026-10-17: item,
# reason, closer, session, time. The settlement fields are the ones the RCA's CHANGE names:
# settled_by, and owner with a due date. Their values are DISTINCT on purpose, so a build
# that treats the two fields as interchangeable cannot pass both branch tests.
RAW_ITEM = "Ollama size 1.05 GB against nvidia-smi 21014 MiB used, un-settled"
SETTLED_CMD = "ssh 192.0.2.2 nvidia-smi --query-gpu=memory.used"
OWNER = "Jas"
DUE = "2026-10-24"


def _row(item, **extra):
    d = {"item": item,
         "reason": "no recorder command named for the flagged size number",
         "closer": "UNTRACKED: session-review deferral with no owner recorded",
         "session": "745a17ba",
         "time": "2026-10-17T06:12:11Z"}
    d.update(extra)
    return json.dumps(d, ensure_ascii=False)


RAW = _row(RAW_ITEM)
SETTLED = _row(RAW_ITEM + " (settled)", settled_by=SETTLED_CMD)
OWNED = _row(RAW_ITEM + " (filed)", owner=OWNER, due=DUE)
BLANK = _row(RAW_ITEM + " (blank)", settled_by="", owner="")


def _repo(tmp, rows):
    """A throwaway repo holding only a deferral ledger, built inside a temp dir and never in
    the tree. No git init: Checker records a note when git declines, and the check under test
    reads the ledger file, not git."""
    d = Path(tmp)
    (d / "workspace").mkdir()
    (d / "workspace" / "deferrals.jsonl").write_text(
        "".join(r + "\n" for r in rows), encoding="utf-8")
    return d


def _verdict(checker):
    """The lines the check printed. self.result's print is a check's only observable surface:
    a check that prints nothing checked nothing."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        check_workspace.check_deferral_settled_or_owner(checker)
    return buf.getvalue()


class DeferralSettledOrOwner(unittest.TestCase):

    def test_the_check_exists_on_the_module(self):
        """RED TODAY: no such attribute. The module-level alias is part of the contract, not
        decoration -- a check that lives only as a method reads, to a test, as a check that
        was never built (the precedent is the alias of check_task_closure_dod at the foot of
        scripts/check_workspace.py)."""
        self.assertTrue(hasattr(check_workspace, "check_deferral_settled_or_owner"),
                        "scripts/check_workspace.py defines no check_deferral_settled_or_owner, "
                        "so a deferral with neither settled_by nor owner advances a session "
                        "unrecorded (workspace/rca/13773fe318de.md)")

    def test_an_unowned_row_fails(self):
        """The defect, asserted as a verdict. Today every row of the live ledger is this shape."""
        with tempfile.TemporaryDirectory() as tmp:
            checker = check_workspace.Checker(_repo(tmp, [RAW]))
            out = _verdict(checker)
        self.assertIn("FAIL", out, "a row with NEITHER settled_by NOR owner did not FAIL: %r" % out)
        self.assertNotIn("PASS", out, "PASS on an un-owned row is the evaporation this closes: %r" % out)
        self.assertIn(LEDGER, out, "the FAIL must name the ledger it is about -- a failure nobody "
                                   "can act on is routed around: %r" % out)

    def test_a_mixed_ledger_fails_and_names_the_offending_row(self):
        """Two settled rows must not pay for a third: a ledger-level ratio passes this ledger,
        and a FAIL that does not name the row cannot be worked. This is the test that stops the
        check from stopping at the first settled row and calling the ledger clean."""
        with tempfile.TemporaryDirectory() as tmp:
            checker = check_workspace.Checker(_repo(tmp, [SETTLED, OWNED, RAW]))
            out = _verdict(checker)
        self.assertIn("FAIL", out, "settled rows covered an un-owned row: %r" % out)
        self.assertNotIn("PASS", out, "a PASS while an un-owned row stands: %r" % out)
        self.assertIn("21014 MiB used", out,
                      "the FAIL must carry the un-owned row's own item text -- the live ledger "
                      "has 2297 such rows, and a FAIL that does not say which row cannot be "
                      "worked: %r" % out)

    def test_a_settled_by_row_passes(self):
        """The settled_by branch: a row naming its own recorder command is not a gap."""
        with tempfile.TemporaryDirectory() as tmp:
            checker = check_workspace.Checker(_repo(tmp, [SETTLED]))
            out = _verdict(checker)
        self.assertIn("PASS", out, "a row carrying a settled_by recorder command should PASS: %r" % out)
        self.assertNotIn("FAIL", out, "a row that names its settlement is not a gap: %r" % out)

    def test_an_owner_row_passes(self):
        """The owner branch, with a value distinct from settled_by: a row filed to a named
        owner is not a gap. Kept a separate test so a build honouring one branch only
        fails the other."""
        with tempfile.TemporaryDirectory() as tmp:
            checker = check_workspace.Checker(_repo(tmp, [OWNED]))
            out = _verdict(checker)
        self.assertIn("PASS", out, "a row filed to a named owner should PASS: %r" % out)
        self.assertNotIn("FAIL", out, "a row filed to a named owner is not a gap: %r" % out)

    def test_blank_fields_are_not_a_settlement(self):
        """A field present and EMPTY settles nothing: 'if "settled_by" in row' passes this row
        while reading as implemented. The RCA asks for a recorder command whose output refutes
        or confirms the number, so the empty string is the silent pass this closes."""
        with tempfile.TemporaryDirectory() as tmp:
            checker = check_workspace.Checker(_repo(tmp, [BLANK]))
            out = _verdict(checker)
        self.assertIn("FAIL", out, "settled_by empty and owner empty counted as settlement: %r" % out)
        self.assertNotIn("PASS", out, "empty fields are the silent pass this closes: %r" % out)

    def test_a_missing_ledger_is_not_a_pass(self):
        """Absence is not cleanliness: the standard check_deferral_numbers itself sets when
        STATE.md cannot be read -- an unreadable file is not a clean file, and a verdict over
        an examined set of zero is not a pass."""
        with tempfile.TemporaryDirectory() as tmp:
            d = _repo(tmp, [RAW])
            (d / LEDGER).unlink()
            checker = check_workspace.Checker(d)
            out = _verdict(checker)
        self.assertIn("FAIL", out, "a missing ledger reported as clean: %r" % out)
        self.assertNotIn("PASS", out, "a missing ledger is not a clean ledger: %r" % out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
