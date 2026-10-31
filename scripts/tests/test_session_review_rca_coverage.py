#!/usr/bin/env python3
"""t813: every extracted finding must have an "event": "rca" row in the review ledger.

RED on main: scripts/session_review.py has no rca_coverage() and no check_rca_coverage(), so every
test here fails with AttributeError for that reason alone, and the last test fails because argparse
rejects the unknown command "rca-coverage" (SystemExit). Nothing in this file depends on the
repository's live state: every findings directory, ledger and baseline is under a temp dir.

Why this is the case the module exists for: rca() writes a report per dispatched finding and logs
{"event": "rca", "key": key} (session_review.py:1141), and status() counts those rows (:1155-1166),
but nothing compares the finding keys on disk against those rows -- so a finding can sit unanalysed
forever and the review closes green. Live instance: workspace/rca/6de92924186c.md is absent while
its four siblings (b971a901ef55, 70128eaf86ae, a82bddbfd935, 64d5b943a4f0) each got a report.
"""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review  # noqa: E402


def key(n):
    """12-hex keys, the shape the real findings/ files use (0187a2dd15d6, 6de92924186c)."""
    return "0a1b2c3d4e%02x" % n


def write_finding(directory, k):
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    (d / (k + ".json")).write_text(
        json.dumps({"key": k, "kind": "deferral", "quote": "quote for " + k}) + "\n",
        encoding="utf-8")


def write_ledger(path, rows, extra_lines=()):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in rows] + list(extra_lines)
    Path(path).write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def rca_row(k, task="t813"):
    """The exact shape rca() logs (session_review.py:1141)."""
    return {"at": "2026-10-08T04:10:59Z", "event": "rca", "key": k,
            "report": "workspace/rca/" + k + ".md", "tasks": [task]}


class RcaCoverageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="t813-sr-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.findings = self.base / "findings"
        self.ledger = self.base / "ledger.jsonl"

    def cover(self):
        return session_review.rca_coverage(findings_dir=self.findings, ledger_path=self.ledger)

    def test_a_finding_with_no_rca_row_is_reported_missing(self):
        a = key(1)
        write_finding(self.findings, a)
        write_ledger(self.ledger, [])
        missing, unreadable = self.cover()
        self.assertEqual([a], list(missing))
        self.assertEqual([], list(unreadable))

    def test_the_result_is_a_pair_of_sorted_lists(self):
        b, a = key(2), key(1)            # created in reverse order on purpose
        write_finding(self.findings, b)
        write_finding(self.findings, a)
        write_ledger(self.ledger, [])
        missing, unreadable = self.cover()
        self.assertEqual([a, b], list(missing))
        self.assertEqual([], list(unreadable))

    def test_a_matching_rca_row_covers_the_key(self):
        a = key(3)
        write_finding(self.findings, a)
        write_ledger(self.ledger, [{"at": "2026-10-08T04:10:59Z", "event": "finding",
                                    "key": a, "kind": "deferral", "quote": "q"}, rca_row(a)])
        missing, unreadable = self.cover()
        self.assertEqual([], list(missing))
        self.assertEqual([], list(unreadable))

    def test_a_finding_under_done_counts_just_like_a_top_level_one(self):
        a = key(4)
        write_finding(self.findings / "done", a)
        write_ledger(self.ledger, [])
        missing, _ = self.cover()
        self.assertEqual([a], list(missing), "rca() moves analysed findings into findings/done/; "
                                            "scanning only the top-level directory hides them")
        write_ledger(self.ledger, [rca_row(a)])
        missing, _ = self.cover()
        self.assertEqual([], list(missing))

    def test_an_event_that_is_not_rca_does_not_count_as_coverage(self):
        a = key(5)
        write_finding(self.findings, a)
        events = ["finding", "covered", "reviewed", "for-jas", "rca-unusable", "extracted"]
        write_ledger(self.ledger, [{"at": "2026-10-08T04:10:59Z", "event": ev, "key": a}
                                   for ev in events])
        missing, unreadable = self.cover()
        self.assertEqual([a], list(missing), "only event == 'rca' is coverage; six other events name "
                                             "this key and none of them analyse it")
        self.assertEqual([], list(unreadable))

    def test_an_rca_row_for_a_different_key_does_not_cover_this_one(self):
        a, b = key(6), key(7)
        write_finding(self.findings, a)
        write_finding(self.findings, b)
        write_ledger(self.ledger, [rca_row(a)])
        missing, _ = self.cover()
        self.assertEqual([b], list(missing))

    def test_a_torn_ledger_line_is_reported_not_swallowed(self):
        a = key(8)
        write_finding(self.findings, a)
        write_ledger(self.ledger, [rca_row(a)],
                     extra_lines=['{"at": "2026-10-08T04:10:59Z", "event": "rca", "key": '])
        missing, unreadable = self.cover()
        self.assertEqual([], list(missing), "the valid row still covers its key")
        self.assertEqual([2], list(unreadable), "the torn line is line 2 of the file (1-based); "
                                                "a torn row must be said, never passed over")

    def test_an_absent_ledger_is_a_gap_not_a_crash(self):
        a = key(9)
        write_finding(self.findings, a)
        missing, unreadable = self.cover()
        self.assertEqual([a], list(missing))
        self.assertEqual([], list(unreadable))

    def test_the_gate_prints_the_difference_and_returns_one(self):
        a, b, c = key(10), key(11), key(12)
        for k in (a, b, c):
            write_finding(self.findings, k)
        write_ledger(self.ledger, [rca_row(a)])
        out = io.StringIO()
        code = session_review.check_rca_coverage(findings_dir=self.findings,
                                                 ledger_path=self.ledger, out=out)
        text = out.getvalue()
        self.assertEqual(1, code)
        self.assertIn(b, text)
        self.assertIn(c, text)
        self.assertNotIn(a, text, "a covered key must not be reported")
        self.assertTrue(any("missing" in line.lower() and "2" in line
                            for line in text.splitlines()),
                        "the gate must print the size of the set difference: " + text)

    def test_the_gate_is_quiet_and_returns_zero_when_every_key_is_covered(self):
        a, b = key(13), key(14)
        write_finding(self.findings, a)
        write_finding(self.findings / "done", b)
        write_ledger(self.ledger, [rca_row(a), rca_row(b)])
        out = io.StringIO()
        code = session_review.check_rca_coverage(findings_dir=self.findings,
                                                 ledger_path=self.ledger, out=out)
        text = out.getvalue()
        self.assertEqual(0, code)
        self.assertNotIn(a, text)
        self.assertNotIn(b, text)

    def test_the_rca_coverage_command_reports_and_fails(self):
        """The command the task names: `session_review.py rca-coverage` must print and exit non-zero.
        main() takes its paths from the module constants, so the constants are redirected at a temp
        tree -- the repository's own workspace/session-reviews/ is never touched."""
        k = key(15)
        saved = (session_review.FINDINGS, session_review.LEDGER)
        tmp = tempfile.TemporaryDirectory(prefix="t813-cli-")
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        write_finding(root / "findings", k)
        write_ledger(root / "ledger.jsonl", [])
        session_review.FINDINGS = root / "findings"
        session_review.LEDGER = root / "ledger.jsonl"
        try:
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    code = session_review.main(["rca-coverage"])
            except BaseException as exc:        # on main argparse rejects the unknown command
                self.fail("session_review.py: 'rca-coverage' is not an accepted command "
                          "(%s: %s)" % (type(exc).__name__, exc))
            self.assertEqual(1, code)
            self.assertIn(k, out.getvalue())
        finally:
            session_review.FINDINGS, session_review.LEDGER = saved


if __name__ == "__main__":
    unittest.main(verbosity=2)
