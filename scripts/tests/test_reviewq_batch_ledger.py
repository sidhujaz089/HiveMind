#!/usr/bin/env python3
"""t1246 (RCA d849179318aa, D1) -- every batch-size COMPUTATION is recorded.

scripts/reviewq.py's cmd_batch prints "next unattended batch: N task(s)" and writes
no row anywhere, and batch_size() computes the keep rate as `kept / len(recent)`
(reviewq.py:341) and throws it away. So the 3/8 that decided "1 task" on 2026-08-26
is unrecoverable from the record: a later RCA cannot re-derive it, and a proposed
denominator (submissions vs task completion) cannot be tested against history.

RED ON MAIN for that reason and no other: reviewq has no ledger path and no writer,
so batch_size() appends nothing and every test fails at "exactly one row recorded".
GREEN when batch_size() appends one parseable row carrying the inputs the rate was
computed from, AND the number cmd_batch PRINTS is the number it RECORDED.

pytest is not installed: every test runs under __main__ at the bottom.
"""
import argparse
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import reviewq

BATCH = 3   # reviewq.BATCH_START is 3 (reviewq.py:245): the batch size every fixture ran under


def verdict_rows(n=8):
    """The 2026-08-26 shape: 3 clean keeps + (n-3) sent-backs, batched, none harness.

    keep rate 3/8 = 0.375, below SHRINK_BELOW (0.60), so the last batch (3) halves to 1.
    """
    return [{"task": f"t1246-{i}", "at": f"2026-10-16T00:00:{i:02d}Z",
             "verdict": reviewq.KEPT if i < 3 else reviewq.SENT_BACK,
             "category": "model" if i < 3 else "wrong-behaviour",
             "batch": BATCH, "why": "fixture"} for i in range(n)]


def run_rows(vrows):
    """One ACCEPTED run per verdict, so no verdict is a kept on a run that delivered nothing."""
    return [{"task": v["task"], "at": v["at"], "outcome": "ACCEPTED",
             "branch": f"coder/{v['task']}"} for v in vrows]


def write_jsonl(path, rows):
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class BatchLedger(unittest.TestCase):
    """Reads and writes only files inside its own temp dir: the live workspace is untouched."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        d = Path(self._tmp.name)
        self._saved = (reviewq.RUNS, reviewq.VERDICTS,
                       getattr(reviewq, "BATCH_DECISIONS", None))
        write_jsonl(d / "agent-runs.jsonl", [])
        write_jsonl(d / "review-verdicts.jsonl", [])
        reviewq.RUNS = d / "agent-runs.jsonl"
        reviewq.VERDICTS = d / "review-verdicts.jsonl"
        reviewq.BATCH_DECISIONS = d / "batch-decisions.jsonl"

    def tearDown(self):
        reviewq.RUNS, reviewq.VERDICTS = self._saved[0], self._saved[1]
        if self._saved[2] is None:
            if hasattr(reviewq, "BATCH_DECISIONS"):
                del reviewq.BATCH_DECISIONS
        else:
            reviewq.BATCH_DECISIONS = self._saved[2]
        self._tmp.cleanup()

    def rows(self):
        """Every row in the ledger. Deliberately NO try/except: a torn line must fail here."""
        p = reviewq.BATCH_DECISIONS
        if not p.is_file():
            return []
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_batch_size_appends_one_row_with_the_inputs_it_used(self):
        self.assertEqual(self.rows(), [])
        v = verdict_rows(8)
        write_jsonl(reviewq.RUNS, run_rows(v))
        n, why = reviewq.batch_size(vs=v)
        after = self.rows()
        self.assertEqual(len(after), 1, f"one computation must write exactly one row: {after}")
        row = after[0]
        self.assertEqual(row["n"], n, f"the recorded n must be the returned n: {row} vs {n}")
        self.assertEqual(n, 1, f"3/8 is below SHRINK_BELOW, so the base 3 halves to 1: got {n}")
        self.assertEqual(row["kept"], 3, row)
        self.assertEqual(row["len"], 8, f"the rate's denominator must be recorded: {row}")
        self.assertAlmostEqual(row["rate"], 3 / 8, places=9)
        self.assertEqual(row["prev"], 3, f"the base it halved from must be recorded: {row}")
        self.assertEqual(row["escaped"], 0, row)
        self.assertEqual(row["excluded_counts"],
                         {"harness": 0, "superseded": 0, "repaired": 0, "delivered_nothing": 0}, row)
        self.assertIsInstance(row["at"], str)
        self.assertTrue(row["at"].endswith("Z"), row["at"])
        self.assertIn("T", row["at"])
        self.assertGreaterEqual(int(row["rule_version"]), 1, row)

    def test_cmd_batch_records_the_number_it_prints(self):
        """The RCA's endpoint: the call that PRINTS the control number must RECORD it."""
        v = verdict_rows(8)
        write_jsonl(reviewq.RUNS, run_rows(v))
        write_jsonl(reviewq.VERDICTS, v)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = reviewq.cmd_batch(argparse.Namespace())
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        m = re.search(r"next unattended batch: (\d+) task", out)
        self.assertIsNotNone(m, f"cmd_batch's printed line changed shape: {out!r}")
        rows = self.rows()
        self.assertEqual(len(rows), 1, f"printing is the moment the number must be recorded: {rows}")
        self.assertEqual(int(m.group(1)), rows[0]["n"],
                         f"the number printed ({m.group(1)}) is not the number recorded ({rows[0]['n']})")
        self.assertEqual(int(m.group(1)), 1, out)

    def test_excluded_verdicts_are_counted_with_four_distinct_values(self):
        """A row carrying only n cannot be audited: the exclusions are recorded as counts."""
        v = verdict_rows(8)
        v.append({"task": "t1246-h1", "at": "2026-10-16T01:00:00Z", "verdict": reviewq.SENT_BACK,
                  "category": "harness", "batch": BATCH, "why": "fixture"})
        v.append({"task": "t1246-h2", "at": "2026-10-16T01:00:01Z", "verdict": reviewq.SENT_BACK,
                  "category": "harness", "batch": BATCH, "why": "fixture"})
        v.append({"task": "t1246-s1", "at": "2026-10-16T01:00:02Z", "verdict": reviewq.SUPERSEDED,
                  "category": "model", "batch": BATCH, "why": "fixture"})
        v.append({"task": "t1246-r1", "at": "2026-10-16T01:00:03Z", "verdict": reviewq.KEPT,
                  "category": "model", "batch": BATCH, "repaired": True, "why": "fixture"})
        write_jsonl(reviewq.RUNS, run_rows(v))
        n, why = reviewq.batch_size(vs=v)
        rows = self.rows()
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(row["kept"], 3, f"a keep that needed repair is not counted clean: {row}")
        self.assertEqual(row["len"], 9, f"harness and superseded rows leave the denominator: {row}")
        self.assertAlmostEqual(row["rate"], 3 / 9, places=9)
        self.assertEqual(n, 1, (n, row))
        self.assertEqual(row["excluded_counts"],
                         {"harness": 2, "superseded": 1, "repaired": 1, "delivered_nothing": 0}, row)

    def test_an_escaped_verdict_is_recorded_and_resets_the_base(self):
        v = [{"task": "t1246-e0", "at": "2026-10-16T00:00:00Z", "verdict": reviewq.KEPT,
              "category": "model", "batch": BATCH, "escaped": True, "repaired": True,
              "why": "found defective after merge"}] + verdict_rows(8)
        write_jsonl(reviewq.RUNS, run_rows(v))
        n, why = reviewq.batch_size(vs=v)
        rows = self.rows()
        self.assertEqual(len(rows), 1, rows)
        row = rows[0]
        self.assertEqual(row["escaped"], 1, row)
        self.assertEqual(row["prev"], 1, f"an escape resets the base it grows from: {row}")
        self.assertEqual(row["kept"], 3, row)
        self.assertEqual(row["len"], 8, f"only verdicts vetted after the escape count: {row}")
        self.assertAlmostEqual(row["rate"], 3 / 8, places=9)
        self.assertEqual(row["n"], n, row)
        self.assertEqual(n, 1, (n, row))
        self.assertEqual(row["excluded_counts"],
                         {"harness": 0, "superseded": 0, "repaired": 0, "delivered_nothing": 0}, row)

    def test_no_row_is_written_when_no_rate_is_computed(self):
        """The no-rate returns print a size but compute no keep rate; a recorded
        rate of 0.0 would be indistinguishable from a real 0% rate, so nothing is written."""
        v = [{"task": "t1246-h1", "at": "2026-10-16T02:00:00Z", "verdict": reviewq.SENT_BACK,
              "category": "harness", "batch": BATCH, "why": "fixture"}]
        write_jsonl(reviewq.RUNS, run_rows(v))
        n, why = reviewq.batch_size(vs=v)
        self.assertEqual(n, BATCH, (n, why))
        self.assertEqual(self.rows(), [], "no rate was computed, so there is no control number to record")

    def test_each_computation_appends_a_new_row(self):
        v = verdict_rows(8)
        write_jsonl(reviewq.RUNS, run_rows(v))
        for i in (1, 2, 3):
            reviewq.batch_size(vs=v)
            self.assertEqual(len(self.rows()), i, f"call {i} must append, never overwrite")


if __name__ == "__main__":
    unittest.main(verbosity=2)
