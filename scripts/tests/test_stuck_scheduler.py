#!/usr/bin/env python3
"""Fail-first oracle for scripts/stuck_scheduler.py (t314).

Incident (t314 note, 2026-09-14): a job on qwen3.8:27b-mtp-bf16 hung 14+ minutes --
no load attempt, nothing in ollama's journal -- while resident gemma4:26b answered in
seconds. inference_health.probe() said UP because it tests the RESIDENT model
(scripts/inference_health.py:100-116), so every standing check missed it.

This oracle pins the DECISION RULE, not the I/O: every input is passed in, so nothing
here touches live state (AGENTS: oracles use temp dirs and fakes only).
Rule, per the task note: stuck = job on M past grace AND M not resident AND no load of M
logged AND no known legitimate holder of the slot (the BUSY shape at
inference_health.py:199-229). Any one conjunct false -> it is loading, running, or a
legit queue, not a wedge.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stuck_scheduler as ss  # noqa: E402

M = "qwen3.8:27b-mtp-bf16"


class DecisionRule(unittest.TestCase):
    def test_wedge_is_detected(self):
        # The 2026-09-14 shape: old job, model never resident, no load line, no holder.
        self.assertTrue(ss.decided(model=M, job_age_s=900.0,
                                   resident=["gemma4:26b"], load_logged=False,
                                   holder_known=False, grace_s=ss.DEFAULT_GRACE_S))

    def test_young_job_is_never_a_wedge(self):
        self.assertFalse(ss.decided(model=M, job_age_s=60.0,
                                    resident=[], load_logged=False,
                                    holder_known=False, grace_s=ss.DEFAULT_GRACE_S))

    def test_boundary_is_strictly_over(self):
        # age == grace is still patience, not stuck.
        self.assertFalse(ss.decided(model="m", job_age_s=ss.DEFAULT_GRACE_S,
                                    resident=[], load_logged=False,
                                    holder_known=False, grace_s=ss.DEFAULT_GRACE_S))

    def test_load_in_progress_is_not_a_wedge(self):
        self.assertFalse(ss.decided(model=M, job_age_s=900.0,
                                    resident=[], load_logged=True,
                                    holder_known=False, grace_s=ss.DEFAULT_GRACE_S))

    def test_resident_is_not_a_wedge(self):
        self.assertFalse(ss.decided(model=M, job_age_s=900.0,
                                    resident=["gemma4:26b", M], load_logged=False,
                                    holder_known=False, grace_s=ss.DEFAULT_GRACE_S))

    def test_known_holder_is_a_queue_not_a_wedge(self):
        # A known dispatch/interactive step holds the -np 1 slot: M is queued, not wedged.
        self.assertFalse(ss.decided(model=M, job_age_s=900.0,
                                    resident=["gemma4:26b"], load_logged=False,
                                    holder_known=True, grace_s=ss.DEFAULT_GRACE_S))

    def test_resident_match_is_by_full_tag(self):
        # "qwen3.8:27b-mtp" must not count as resident for "qwen3.8:27b-mtp-bf16".
        self.assertTrue(ss.decided(model=M, job_age_s=900.0,
                                   resident=["qwen3.8:27b-mtp"], load_logged=False,
                                   holder_known=False, grace_s=ss.DEFAULT_GRACE_S))

    def test_default_grace_is_few_minutes(self):
        self.assertGreaterEqual(ss.DEFAULT_GRACE_S, 120)
        self.assertLessEqual(ss.DEFAULT_GRACE_S, 900)


class EventRow(unittest.TestCase):
    def test_row_is_error_scan_shape(self):
        # Rows in workspace/run-logs/ollama-events.jsonl carry a status never "ok"
        # (scripts/ollama_events.py:12-15) so the scanner surfaces them; ledger rows
        # carry at_epoch (scripts/ollama_events.py:60).
        row = ss.event_row(model=M, job_age_s=900.0, grace_s=ss.DEFAULT_GRACE_S)
        self.assertEqual(row["status"], "stuck-scheduler")
        self.assertNotEqual(row["status"], "ok")
        self.assertEqual(row["model"], M)
        self.assertGreater(row["job_age_s"], row["grace_s"])
        self.assertIsInstance(row["at_epoch"], (int, float))
        self.assertIn("restart", row["say"].lower())
        json.dumps(row)  # must be serialisable

    def test_record_appends_one_line_to_given_path(self):
        # record() writes to the path it is given -- the oracle points it at a temp dir,
        # never the live ledger.
        row = ss.event_row(model="m", job_age_s=900.0, grace_s=ss.DEFAULT_GRACE_S)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "events.jsonl"
            ss.record(row, p)
            ss.record(row, p)
            lines = p.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertEqual(json.loads(line)["status"], "stuck-scheduler")



class WaitingOnTheModel(unittest.TestCase):
    """2026-10-01: a build still baselining its sandbox has not asked the model anything; judging it
    from its start restarted Ollama and killed it and two RCAs."""

    def _job(self, text):
        d = Path(tempfile.mkdtemp())
        (d / "j.log").write_text(text)
        real = ss.REPO
        ss.REPO = d
        try:
            return ss.model_wait({"pid": 7, "kind": "production"}, 10**10,
                                 history=[{"event": "started", "pid": 7, "log": "j.log"}])
        finally:
            ss.REPO = real

    def test_baselining_build_is_not_judged(self):
        self.assertIsNone(self._job("  baselining the sandbox … 11 suite(s) already failing\n"))

    def test_attempt_in_flight_is_judged(self):
        self.assertIsNotNone(self._job("  context: x.py\n\n  attempt 1 … "))

    def test_finished_attempt_is_not_in_flight(self):
        self.assertIsNone(self._job("  attempt 1 … asked for 1 file(s), gave 1 new (10 tok, 3s)\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
