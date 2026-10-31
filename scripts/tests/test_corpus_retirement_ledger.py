#!/usr/bin/env python3
"""t1447: a deliberate retirement must not be reported as a loss.

RCA workspace/rca/f1f35e0eddc4.md:33 -- the assistant "saw files missing, assumed
loss, and restored them without checking whether the deletion was deliberate."
Two untracked corpus CSVs were deleted on Jas's instruction on 2026-08-24 (task
t131, decision D-27, snapshot ~/ledger-snapshots/2026-08-24-pre-routing-delete/);
the manifest diff on 2026-08-27 called them missing (scripts/backup_corpora.py:463
prints '[LOST]' for every path in old-minus-new) and the restore followed.

The class closes when the tool that SEES the absence is the tool that has to read
the record. This fixture pins that: a missing path with a retirement row is
DELIBERATE and cites the row; a missing path with no row stays a loss, in the
words it uses today. Built from the real ledger's row shape (t809's spec: ts,
session, quote, actor, paths, reason, task, deleted), not an invented one.
"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import backup_corpora

MANIFEST = backup_corpora.MANIFEST

# Two DISTINCT corpus paths that share a stem, on purpose: a join that matches on a
# substring of 'routing' clears a path whose row is about the other file, and this
# fixture fails it.
RETIRED = "chat/notes/routing.csv"
UNRECORDED = "chat/notes/routing-2.csv"
REASON = "D-27: MCP made the routing table unnecessary"
TASK = "t131"


def _bundle(d, rels):
    """A backup bundle directory whose MANIFEST.sha256 lists `rels`, with the files."""
    b = Path(d)
    b.mkdir(parents=True, exist_ok=True)
    lines = []
    for rel in rels:
        f = b / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(rel + "\n", encoding="utf-8")
        lines.append("0" * 64 + "  " + rel)
    (b / MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return b


def _row(path, reason=REASON, task=TASK, deleted="yes"):
    """A ledger row in the shape scripts/session_end.py record_retirements() writes."""
    return {"ts": "2026-08-24T23:18:00Z", "session": "6cc34e3b",
            "quote": "we decided to delete those CSV files. they serve no purpose now.",
            "actor": "jas", "paths": [path], "reason": reason, "task": task,
            "deleted": deleted}


def _mentioned(text, path):
    return [ln for ln in text.splitlines() if path in ln]


class RetirementClassification(unittest.TestCase):
    def test_load_retirements_keys_the_ledger_by_corpus_path(self):
        with tempfile.TemporaryDirectory() as d:
            led = Path(d) / "retirements.jsonl"
            led.write_text(json.dumps(_row(RETIRED)) + "\n", encoding="utf-8")
            got = backup_corpora.load_retirements(led)
            self.assertIsInstance(got, dict)
            self.assertIn(RETIRED, got, "keyed by the row's paths entries, verbatim")
            self.assertEqual(got[RETIRED].get("reason"), REASON)
            self.assertEqual(got[RETIRED].get("task"), TASK)

    def test_a_box_with_no_ledger_yet_still_runs(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(backup_corpora.load_retirements(Path(d) / "absent.jsonl"), {})

    def test_the_gate_reads_the_ledger_the_writer_writes(self):
        """Anti-duplicate: a second ledger file for this class is the drift shape."""
        self.assertEqual(Path(str(backup_corpora.RETIREMENT_LEDGER)).resolve(),
                         (ROOT / "workspace" / "retirements.jsonl").resolve())

    def test_a_missing_path_with_no_row_is_still_a_loss(self):
        with tempfile.TemporaryDirectory() as d:
            old = _bundle(Path(d) / "old", [UNRECORDED])
            new = _bundle(Path(d) / "new", [])
            out = io.StringIO()
            with redirect_stdout(out):
                backup_corpora.diff_backups(old, new, retirements={})
            text = out.getvalue()
            self.assertTrue(any("LOST" in ln and UNRECORDED in ln for ln in text.splitlines()),
                            "an unrecorded absence must still be reported as a loss: " + text)
            self.assertFalse(any("DELIBERATE" in ln and UNRECORDED in ln for ln in text.splitlines()),
                             "an unrecorded absence must not be called deliberate: " + text)

    def test_a_retired_path_is_deliberate_and_cites_its_row(self):
        with tempfile.TemporaryDirectory() as d:
            old = _bundle(Path(d) / "old", [RETIRED])
            new = _bundle(Path(d) / "new", [])
            out = io.StringIO()
            with redirect_stdout(out):
                backup_corpora.diff_backups(old, new, retirements={RETIRED: _row(RETIRED)})
            text = out.getvalue()
            self.assertTrue(any("DELIBERATE" in ln and RETIRED in ln for ln in text.splitlines()),
                            "a recorded retirement must not be reported as a loss: " + text)
            self.assertFalse(any("LOST" in ln and RETIRED in ln for ln in text.splitlines()),
                             "the same path must not be classified LOST and DELIBERATE: " + text)
            self.assertTrue(any(REASON in ln for ln in text.splitlines()),
                            "the report must carry the row's reason, not just a label: " + text)
            self.assertTrue(any(TASK in ln for ln in text.splitlines()),
                            "the report must cite the task that recorded the decision: " + text)

    def test_a_row_about_another_path_does_not_clear_this_one(self):
        with tempfile.TemporaryDirectory() as d:
            old = _bundle(Path(d) / "old", [RETIRED])
            new = _bundle(Path(d) / "new", [])
            out = io.StringIO()
            with redirect_stdout(out):
                backup_corpora.diff_backups(old, new, retirements={UNRECORDED: _row(UNRECORDED)})
            text = out.getvalue()
            self.assertFalse(any("DELIBERATE" in ln and RETIRED in ln for ln in text.splitlines()),
                             "only a row whose path key EQUALS the manifest key may clear it: " + text)
            self.assertTrue(any("LOST" in ln and RETIRED in ln for ln in text.splitlines()),
                             "an unexplained absence stays a loss: " + text)

    def test_a_row_that_records_no_delete_does_not_explain_an_absence(self):
        with tempfile.TemporaryDirectory() as d:
            old = _bundle(Path(d) / "old", [RETIRED])
            new = _bundle(Path(d) / "new", [])
            out = io.StringIO()
            with redirect_stdout(out):
                backup_corpora.diff_backups(old, new, retirements={RETIRED: _row(RETIRED, deleted="no")})
            text = out.getvalue()
            self.assertFalse(any("DELIBERATE" in ln and RETIRED in ln for ln in text.splitlines()),
                             'a row with deleted:"no" is a discussion, not a retirement: ' + text)
            self.assertTrue(any("LOST" in ln and RETIRED in ln for ln in text.splitlines()),
                             'a row with deleted:"no" leaves the absence unexplained: ' + text)

    def test_the_real_ledger_rows_become_keys_of_the_default_load(self):
        """Real artefact: every path the real ledger retires must be findable by the gate."""
        real = ROOT / "workspace" / "retirements.jsonl"
        got = backup_corpora.load_retirements()
        self.assertIsInstance(got, dict)
        if real.is_file():
            for line in real.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                for p in row.get("paths") or []:
                    self.assertIn(p, got,
                                  f"the writer records a retirement for {p} and the gate cannot find it")


if __name__ == "__main__":
    unittest.main(verbosity=2, exit=True)
