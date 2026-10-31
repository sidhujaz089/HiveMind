#!/usr/bin/env python3
"""t1248 -- a decisions ledger row at every DECISIONS.md append.

The question RCA d8d8f7c13bee (DE-3) could not answer by machine -- 'was decision
D-NN recorded, and where?' -- is answered today by a human grep of
docs/DECISIONS.md plus a git pickaxe on -S 'D-35'. This suite pins the machine
answer: scripts/decision_ledger.py turns every '## D-NN' / '### D-NN' heading of
the REAL docs/DECISIONS.md into a ledger row carrying
{id, date, session, commit, supersedes, amended_by, status}, appended as JSONL to
a caller-supplied path, idempotently -- and writes a row for a decision planted in
a temp document, so a future D-40 gets a row with nobody remembering to write one.

Red on main for one reason and one reason only: scripts/decision_ledger.py does
not exist. The module is loaded by path inside every test (never imported at top
level), so a missing writer shows as a named failure on every test.
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = ROOT / "docs" / "DECISIONS.md"
MODULE = ROOT / "scripts" / "decision_ledger.py"
FIELDS = ("amended_by", "commit", "date", "id", "session", "status", "supersedes")
ABSENT = "unrecorded"
DOC = ("# DECISIONS\n\n## D-40 — Planted by t1248 (2026-10-16)\n\n"
       "**2026-10-16, Jas.** Reasoning goes here.\n")


def load_writer(test):
    """Import scripts/decision_ledger.py by path. FAIL (not crash) if it is absent."""
    if not MODULE.is_file():
        test.fail("scripts/decision_ledger.py is missing -- the ledger writer this task "
                  "adds does not exist, so 'was D-NN recorded, and where?' has no "
                  "machine answer (RCA d8d8f7c13bee, DE-3).")
    spec = importlib.util.spec_from_file_location("decision_ledger_t1248", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestDecisionLedger(unittest.TestCase):

    def test_writer_module_exists(self):
        load_writer(self)

    def test_every_heading_in_the_real_DECISIONS_md_becomes_one_row(self):
        m = load_writer(self)
        import re
        text = DECISIONS.read_text(encoding="utf-8")  # whole file: a record is never sliced
        rows = m.parse_decisions(text)
        headings = re.findall(r"(?m)^#{2,4} (D-\d+)\b", text)
        self.assertTrue(headings, "docs/DECISIONS.md has no D-NN heading to test against")
        self.assertEqual([r["id"] for r in rows], headings,
                         "parse_decisions must return exactly one row per "
                         "2-to-4-hash 'D-NN' heading, in document order -- "
                         "docs/DECISIONS.md:18 uses '### D-36 . ...' and "
                         "docs/DECISIONS.md:876 uses '## D-35 — ...'")
        for r in rows:
            self.assertEqual(tuple(sorted(r)), FIELDS,
                             "every ledger row carries exactly the fields %s" % (FIELDS,))

    def test_real_rows_carry_the_dates_and_amendments_the_file_records(self):
        m = load_writer(self)
        rows = m.parse_decisions(DECISIONS.read_text(encoding="utf-8"))
        by_id = {}
        for r in rows:
            by_id.setdefault(r["id"], r)  # first occurrence; D-36 was renumbered, see :936
        self.assertEqual(by_id["D-35"]["date"], "2026-09-11",
                         "docs/DECISIONS.md:878 opens D-35 with '**2026-09-11, Jas.**'")
        self.assertIn("2026-09-28", by_id["D-35"]["amended_by"],
                      "D-35's body records 'AMENDED 2026-09-28, Jas: ...'")
        self.assertEqual(by_id["D-36"]["date"], "2026-10-01",
                         "docs/DECISIONS.md:18 is '### D-36 . 2026-10-01 . SETTLED . ...'")
        self.assertEqual(by_id["D-36"]["status"], "SETTLED",
                         "status comes from the heading's dot-delimited token")
        self.assertEqual(by_id["D-37"]["date"], "2026-10-06",
                         "docs/DECISIONS.md:934 ends '... (2026-10-06)'")
        self.assertEqual(by_id["D-37"]["supersedes"], "D-20",
                         "docs/DECISIONS.md:934 reads 'supersedes D-20 (2026-10-06)'")
        self.assertEqual(by_id["D-39"]["date"], "2026-10-13",
                         "docs/DECISIONS.md:985 ends '... (2026-10-13)'")
        self.assertEqual(by_id["D-36"]["supersedes"], ABSENT,
                         "an entry that supersedes nothing records %r, not a blank" % ABSENT)

    def test_a_planted_heading_writes_one_row_to_the_ledger(self):
        m = load_writer(self)
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "decision-ledger.jsonl"
            written = m.record(ledger, DOC, session="t1248", commit="a1b2c3d")
            self.assertEqual([r["id"] for r in written], ["D-40"])
            rows = m.read_ledger(ledger)
            self.assertEqual(len(rows), 1)
            self.assertEqual(tuple(sorted(rows[0])), FIELDS)
            self.assertEqual(rows[0]["id"], "D-40")
            self.assertEqual(rows[0]["date"], "2026-10-16",
                             "the date in the planted heading, not today's date")
            self.assertEqual(rows[0]["session"], "t1248")
            self.assertEqual(rows[0]["commit"], "a1b2c3d")
            self.assertEqual(rows[0]["status"], ABSENT,
                             "a heading with no status word records the visible "
                             "placeholder %r, never a silent blank" % ABSENT)
            self.assertEqual(rows[0]["amended_by"], ABSENT)
            self.assertEqual(rows[0]["supersedes"], ABSENT)
            lines = ledger.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1, "one JSON object per line")
            self.assertEqual(sorted(json.loads(lines[0])), FIELDS)

    def test_a_prose_mention_of_a_decision_id_is_not_a_decision(self):
        m = load_writer(self)
        prose = ("Some notes that cite `D-35` and D-36 as the manifest (2026-10-06).\n"
                 "Records written before this date that cite D-36 for the manifest "
                 "mean this entry.\n")
        self.assertEqual(m.parse_decisions(prose), [],
                         "ids mentioned in prose are not decision entries; only a heading "
                         "starts one -- docs/DECISIONS.md:936 is exactly this sentence")

    def test_re_recording_the_same_document_appends_nothing(self):
        m = load_writer(self)
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "decision-ledger.jsonl"
            m.record(ledger, DOC, session="t1248", commit="a1b2c3d")
            again = m.record(ledger, DOC, session="t1248", commit="a1b2c3d")
            self.assertEqual(again, [], "an unchanged decision must not gain a second row")
            self.assertEqual(len(m.read_ledger(ledger)), 1)

    def test_an_amendment_appends_a_second_row_for_the_same_id(self):
        m = load_writer(self)
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "decision-ledger.jsonl"
            m.record(ledger, DOC, session="t1248", commit="a1b2c3d")
            amended = DOC.replace("Reasoning goes here.",
                                  "Reasoning goes here. AMENDED 2026-11-01, Jas: more.")
            written = m.record(ledger, amended, session="t1249", commit="e4f5a6b")
            self.assertEqual([r["id"] for r in written], ["D-40"],
                             "a changed entry must record again: dedup is by CONTENT, "
                             "not by id, or an amended decision (D-35, 2026-09-28) vanishes")
            rows = m.read_ledger(ledger)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["amended_by"], ABSENT)
            self.assertEqual(rows[1]["amended_by"], "2026-11-01, Jas")
            self.assertEqual(rows[1]["date"], "2026-10-16",
                             "the entry keeps its own date; the amendment goes in amended_by")
            self.assertEqual(rows[1]["session"], "t1249")
            self.assertEqual(rows[1]["commit"], "e4f5a6b")

    def test_existing_ledger_rows_are_never_rewritten(self):
        m = load_writer(self)
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "decision-ledger.jsonl"
            prior = {"id": "D-01", "date": "2026-01-01", "session": "s0",
                     "commit": "deadbee", "supersedes": ABSENT,
                     "amended_by": ABSENT, "status": "SETTLED"}
            ledger.write_text(json.dumps(prior) + "\n", encoding="utf-8")
            m.record(ledger, DOC, session="t1248", commit="a1b2c3d")
            rows = m.read_ledger(ledger)
            self.assertEqual([r["id"] for r in rows], ["D-01", "D-40"],
                             "the ledger is append-only: a prior row is never "
                             "rewritten, dropped or reordered")
            self.assertEqual(rows[0]["commit"], "deadbee")


if __name__ == "__main__":
    unittest.main(verbosity=2)
