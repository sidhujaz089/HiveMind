"""t1087 -- a deferral is a record, not a sentence.

workspace/rca/a61a86bb5cb0.md records the assistant saying "Anything Q16 flags as
un-migratable I'll leave and record rather than force" (turn 255 of 395). The record half
has no endpoint: scripts/session_review.py's _keep is the one place a reviewed quote becomes
a finding whose disposition is deferred, and it writes the finding JSON, a ledger.jsonl line,
and no machine-readable row naming item / reason / closer / session / time. This suite is
that missing row.

RED ON MAIN FOR ONE REASON (as scoped 2026-10-12): session_review had no record_deferral, no
deferral_rows, no DEFERRAL_LEDGER, no DEFERRAL_ROW_FIELDS, and _keep emitted no deferral row.
The implementation landed in 8441f8f95; the ten behaviour tests are green on main. The eleventh
was red for its OWN bug: setUp redirects DEFERRAL_LEDGER to a temp dir before every test, so the
test that asserted the constant's value inside that class could not pass for any implementation.
It now asserts the value captured at import (DEFAULT_DEFERRAL_LEDGER), which is the fact the
assertion was written to pin.

No pytest (it is not installed). No model call, no subprocess, no live server, no live
state: every path written is under tempfile.mkdtemp(); QUOTE deliberately carries no tNNN
token so _keep's cover search never opens the taskstore; and every module attribute this
suite redirects (FINDINGS, DEFERRAL_LEDGER, _log, _archive_cover) is restored in tearDown,
so a run leaves the worktree byte-identical.

Run:  python3 scripts/tests/test_t1087_deferral_row.py
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # scripts/tests -> repo root
sys.path.insert(0, str(ROOT / "scripts"))
import session_review                               # noqa: E402

# Captured AT IMPORT, before setUp can redirect the attribute. "Where does the deferral ledger
# live?" is a fact about the module's default; asking it inside a class whose setUp has already
# replaced the default asks a question that has no answer (t1087, 2026-10-20).
DEFAULT_DEFERRAL_LEDGER = getattr(session_review, "DEFERRAL_LEDGER", "")

# Distinct values everywhere, so a row that copies one field into the next cannot pass.
ITEM = "Q16 flags some rows as un-migratable"
REASON = "the migration would change meaning, so it is left and recorded, not forced"
CLOSER = "t1087"
SESSION = "a61a86bb5cb0"
OTHER_ITEM = "a second deferred item in the same session"
OTHER_REASON = "the quote named no tracker the reviewer could resolve"
OTHER_CLOSER = "t999"
OTHER_SESSION = "721de5a0"
LONG_REASON = "reason that runs past any plausible cap: " + ("deferral " * 60)
UNOWNED = "UNTRACKED: session-review deferral with no owner recorded"

# No tNNN token in the quote: _keep's cover search runs re.findall over the quote and a task
# id would send it to the live taskstore, and an oracle must never touch live state.
QUOTE = "Anything Q16 flags as un-migratable I'll leave and record rather than force"
REQ = {"id": SESSION, "harness": "claude-code", "_candidates": []}


class DeferralRow(unittest.TestCase):
    """Every write lands inside this test's own temporary directory."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="t1087-"))
        self.ledger = self.dir / "deferrals.jsonl"
        self.saved = {}
        for name, value in (("FINDINGS", self.dir / "findings"),
                            ("DEFERRAL_LEDGER", self.ledger),
                            ("_archive_cover", lambda quote: "")):
            self.saved[name] = getattr(session_review, name, None)
            setattr(session_review, name, value)
        self.logs = []
        self.saved["_log"] = getattr(session_review, "_log", None)
        session_review._log = lambda row: self.logs.append(row)

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(session_review, name, value)

    def finding(self, **over):
        rec = {"kind": "deferral", "quote": QUOTE, "why_missed": REASON, "tracked_as": CLOSER}
        rec.update(over)
        return rec

    # ---- the writer ----------------------------------------------------------------

    def test_the_deferral_writer_and_reader_exist(self):
        for name in ("record_deferral", "deferral_rows"):
            self.assertTrue(callable(getattr(session_review, name, None)),
                            f"scripts/session_review.py has no {name}: the RCA's 'leave and "
                            "record' has a record half with no endpoint "
                            "(workspace/rca/a61a86bb5cb0.md)")

    def test_the_row_fields_are_named_and_the_ledger_is_in_workspace(self):
        self.assertEqual(set(getattr(session_review, "DEFERRAL_ROW_FIELDS", ())),
                         {"item", "reason", "closer", "session", "time"},
                         "the row carries the five fields the task names; a subset is a "
                         "deferral nobody can close")
        self.assertEqual(str(getattr(session_review, "DEFERRAL_LEDGER", "")),
                         str(ROOT / "workspace" / "deferrals.jsonl"),
                         "the deferral ledger is a workspace ledger, beside the verification one")

    def test_recording_appends_one_whole_row_with_distinct_values(self):
        self.assertTrue(hasattr(session_review, "record_deferral"), "no record_deferral to call")
        row = session_review.record_deferral(ITEM, REASON, CLOSER, SESSION, ledger=self.ledger)
        rows = session_review.deferral_rows(ledger=self.ledger)
        self.assertEqual(len(rows), 1, f"expected exactly one row, got {len(rows)}")
        self.assertEqual(set(rows[0]), {"item", "reason", "closer", "session", "time"},
                         f"the row's keys are {sorted(rows[0])}; the five fields are the contract")
        self.assertEqual(rows[0]["item"], ITEM)
        self.assertEqual(rows[0]["reason"], REASON)
        self.assertEqual(rows[0]["closer"], CLOSER)
        self.assertEqual(rows[0]["session"], SESSION)
        self.assertEqual(str(rows[0]["time"]).strip(), rows[0]["time"])
        self.assertTrue(str(rows[0]["time"]).strip(), "the time field is empty")
        self.assertEqual(row.get("item"), ITEM, "record_deferral must return the row it wrote")
        first = self.ledger.read_text(encoding="utf-8")
        session_review.record_deferral(OTHER_ITEM, OTHER_REASON, OTHER_CLOSER, OTHER_SESSION,
                                       ledger=self.ledger)
        self.assertTrue(self.ledger.read_text(encoding="utf-8").startswith(first),
                        "the ledger is append-only: an existing row was rewritten")
        rows = session_review.deferral_rows(ledger=self.ledger)
        self.assertEqual([r["item"] for r in rows], [ITEM, OTHER_ITEM])
        self.assertEqual(rows[1]["closer"], OTHER_CLOSER)

    def test_a_long_reason_is_stored_whole(self):
        self.assertTrue(hasattr(session_review, "record_deferral"), "no record_deferral to call")
        session_review.record_deferral(ITEM, LONG_REASON, CLOSER, SESSION, ledger=self.ledger)
        stored = session_review.deferral_rows(ledger=self.ledger)[0]["reason"]
        self.assertEqual(stored, LONG_REASON,
                         "a deferral reason is a record: a slice loses the answer silently")

    def test_a_blank_field_is_refused_before_anything_is_written(self):
        self.assertTrue(hasattr(session_review, "record_deferral"), "no record_deferral to call")
        for field, args in (
                ("item", ("", REASON, CLOSER, SESSION)),
                ("reason", (ITEM, None, CLOSER, SESSION)),
                ("closer", (ITEM, REASON, "   ", SESSION)),
                ("session", (ITEM, REASON, CLOSER, ""))):
            with self.assertRaises(Exception, msg=f"an empty {field} must be refused"):
                session_review.record_deferral(*args, ledger=self.ledger)
            self.assertFalse(self.ledger.exists(),
                             f"an empty {field} was refused AFTER writing a ledger file")

    def test_a_missing_ledger_reads_as_no_deferrals(self):
        self.assertEqual(session_review.deferral_rows(ledger=self.dir / "absent.jsonl"), [],
                         "'no deferral recorded' must read as [], not as an error")

    def test_a_torn_final_line_is_a_reported_loss_not_a_dropped_row(self):
        self.ledger.write_text(
            json.dumps({"item": ITEM, "reason": REASON, "closer": CLOSER, "session": SESSION,
                        "time": "2026-10-13T03:12:19Z"}) + "\n"
            + json.dumps({"item": OTHER_ITEM, "reason": OTHER_REASON, "closer": OTHER_CLOSER,
                          "session": OTHER_SESSION, "time": "2026-10-13T03:12:20Z"}) + "\n"
            + "\n"
            + '{"item": "half a re',
            encoding="utf-8")
        rows = session_review.deferral_rows(ledger=self.ledger)
        self.assertEqual(len(rows), 3, f"a torn line must be reported, not dropped: {rows}")
        self.assertEqual(rows[0]["item"], ITEM)
        self.assertEqual(rows[1]["closer"], OTHER_CLOSER)
        self.assertEqual(rows[2].get("_unparsable"), '{"item": "half a re',
                         "the torn line must come back whole under an _unparsable key")

    # ---- the endpoint --------------------------------------------------------------

    def test_a_deferral_finding_emits_exactly_one_row(self):
        self.assertTrue(hasattr(session_review, "record_deferral"), "no record_deferral to call")
        new, covered = session_review._keep(Path("req-1.json"), REQ, [self.finding()])
        rows = session_review.deferral_rows(ledger=self.ledger)
        self.assertEqual(new, 1, f"_keep reported {new} new findings")
        self.assertEqual(covered, 0, f"_keep reported {covered} covered")
        self.assertEqual(len(rows), 1, f"expected one deferral row, got {len(rows)}")
        self.assertEqual(rows[0]["item"], QUOTE, "the row's item is the deferred quote")
        self.assertEqual(rows[0]["reason"], REASON)
        self.assertEqual(rows[0]["closer"], CLOSER)
        self.assertEqual(rows[0]["session"], SESSION)
        self.assertTrue(any(l.get("event") == "finding" for l in self.logs),
                        "_keep stopped logging the finding it writes")

    def test_a_deferral_with_no_tracker_recorded_is_named_as_unowned(self):
        self.assertTrue(hasattr(session_review, "record_deferral"), "no record_deferral to call")
        session_review._keep(Path("req-2.json"), REQ, [self.finding(tracked_as="")])
        rows = session_review.deferral_rows(ledger=self.ledger)
        self.assertEqual(len(rows), 1, f"expected one deferral row, got {len(rows)}")
        self.assertEqual(rows[0]["closer"], UNOWNED,
                         "a deferral with no owner must say so, not close it silently")

    def test_a_finding_that_is_not_a_deferral_emits_no_deferral_row(self):
        self.assertTrue(hasattr(session_review, "record_deferral"), "no record_deferral to call")
        session_review._keep(Path("req-3.json"), REQ, [self.finding(kind="unresolved")])
        self.assertEqual(session_review.deferral_rows(ledger=self.ledger), [],
                         "the ledger must keep meaning 'deferral': a non-deferral wrote a row")

    def test_the_finding_is_written_even_when_the_ledger_cannot_be(self):
        self.assertTrue(hasattr(session_review, "record_deferral"), "no record_deferral to call")
        blocked = self.dir / "not-a-directory"        # a FILE where a parent dir must be
        blocked.write_text("x", encoding="utf-8")
        session_review.DEFERRAL_LEDGER = blocked / "ledger.jsonl"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            new, covered = session_review._keep(Path("req-4.json"), REQ, [self.finding()])
        detail = err.getvalue()
        self.assertEqual(new, 1, f"_keep reported {new} new findings")
        self.assertEqual(covered, 0)
        self.assertTrue(list((self.dir / "findings").glob("*.json")),
                        "a failing ledger writer must not lose the finding JSON")
        self.assertIn("deferral row not written", detail,
                      f"a failed ledger write is silent: {detail!r}")
        self.assertIn("NotADirectoryError", detail,
                      f"the handler must name the exception it caught, not swallow it: {detail!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
