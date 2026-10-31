#!/usr/bin/env python3
"""t025 oracle: the ledger's section 12 load log is generated from mcp-calls.jsonl.

The defect (L-076 class, docs/CHECKS.md): the FAILED-LOAD RULE depends on a
session that could not read the ledger choosing to report it, so the load log
undercounts exactly the event it exists to count. The call log is written by
the server and already distinguishes a read that succeeded (outcome "ok")
from one that was refused (outcome "refused" + detail), so the log can be
generated from it instead of from self-report.

Temp dirs and fakes only; the live ledger and the live mcp-calls.jsonl are
never touched.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import check_ledger  # noqa: E402

LEDGER = "chat/projects/family/State_Ledger_Family_Project.txt"


def row(ts, outcome, target=LEDGER, detail=None, via=None, client=None):
    r = {"ts": ts, "tool": "read", "target": target, "outcome": outcome}
    if detail is not None:
        r["detail"] = detail
    if via is not None:
        r["via"] = via
    if client is not None:
        r["client"] = client
    if outcome == "ok":
        r["bytes"] = 107744
    return r


def write_calls(tmp, rows):
    p = Path(tmp) / "mcp-calls.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


class TestLoadLog(unittest.TestCase):
    def test_failed_load_appears_without_self_report(self):
        # The whole point: the failing session never volunteers the entry.
        with tempfile.TemporaryDirectory() as tmp:
            p = write_calls(tmp, [
                row("2026-08-19T05:28:13Z", "ok"),
                row("2026-08-20T09:00:00Z", "refused", detail="path-denied"),
            ])
            lines = check_ledger.load_log(p)
            joined = "\n".join(lines)
            self.assertIn("FAILED-LOAD", joined)
            self.assertIn("2026-08-20T09:00:00Z", joined)
            self.assertIn("path-denied", joined)

    def test_successful_load_appears_as_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = write_calls(tmp, [row("2026-08-19T05:28:13Z", "ok")])
            joined = "\n".join(check_ledger.load_log(p))
            self.assertIn("LOADED", joined)
            self.assertNotIn("FAILED-LOAD", joined)

    def test_non_ledger_reads_are_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = write_calls(tmp, [
                row("2026-08-18T19:03:46Z", "ok", target="AGENTS.md"),
                row("2026-08-19T00:37:23Z", "refused", target="/mcp",
                    detail="no-authorization-header"),
            ])
            self.assertEqual(check_ledger.load_log(p), [])

    def test_events_sorted_by_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = write_calls(tmp, [
                row("2026-08-21T00:00:00Z", "ok"),
                row("2026-08-19T00:00:00Z", "refused", detail="too-large:59150535"),
            ])
            lines = check_ledger.load_log(p)
            self.assertEqual(len(lines), 2)
            self.assertIn("2026-08-19T00:00:00Z", lines[0])
            self.assertIn("2026-08-21T00:00:00Z", lines[1])

    def test_generated_body_keeps_the_census_honest(self):
        # Section 12 is a section of '- ' entries; splice the generated body
        # into a full 12-section fake ledger, declare the count, expect COMPLETE.
        with tempfile.TemporaryDirectory() as tmp:
            p = write_calls(tmp, [
                row("2026-08-19T05:28:13Z", "ok"),
                row("2026-08-20T09:00:00Z", "refused", detail="path-denied"),
            ])
            body = check_ledger.load_log(p)
            for line in body:
                self.assertTrue(line.startswith("- "), line)
            text = ["**Last updated:** 2026-08-20 (fake)"]
            for i in range(1, 12):
                text.append("## %d. Section %d (1)" % (i, i))
                text.append("- filler")
            text.append("## 12. Load log (%d)" % len(body))
            text.extend(body)
            text.append("**Load-check value:** canary")
            findings, _ = check_ledger.census("\n".join(text))
            self.assertEqual(findings, [])

    def test_malformed_row_is_reported_not_swallowed(self):
        # failure-schema 'swallowed-exception-introduced': a row that cannot
        # be parsed must surface with its line number, never be skipped.
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "mcp-calls.jsonl"
            good = {"ts": "2026-08-19T05:28:13Z", "tool": "read",
                    "target": "x", "outcome": "ok"}
            p.write_text(json.dumps(good) + "\nnot-json\n", encoding="utf-8")
            with self.assertRaises(ValueError) as ctx:
                check_ledger.load_log(p)
            self.assertIn("line 2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
