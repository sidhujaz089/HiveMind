#!/usr/bin/env python3
"""t813 (half 2): check_workspace must ask session_review for RCA coverage and FAIL on gaps.

RED on main: scripts/check_workspace.py's Checker has no check_rca_coverage method, so every test
here fails with AttributeError for that reason alone, and the last test fails because
"self.check_rca_coverage" does not appear in Checker.run -- the exact defect this file records at
:736-739, "built with tests and run by nothing". A method that is not registered is decoration:
session_end.py commits on this file's exit code, so an unregistered check protects nothing.

Every tree here is a temp dir shaped like the real one:
workspace/session-reviews/{findings/, findings/done/, ledger.jsonl, rca-coverage-baseline.json}.
Nothing under the repository is read or written. Checker.__init__ runs `git rev-parse
--git-common-dir` in the temp root (a subprocess with timeout=10 inside the module at :131-137);
git declines in a non-repo and the constructor records that in live_note -- no crash, and the new
method must depend only on self.repo, never on self.live.
"""
import contextlib
import inspect
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402

TITLE = "Every finding has an RCA row"


def key(n):
    return "1f2e3d4c5b%02x" % n


def rca_row(k):
    return {"at": "2026-10-08T04:10:59Z", "event": "rca", "key": k,
            "report": "workspace/rca/" + k + ".md", "tasks": ["t813"]}


class CheckerRcaCoverageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="t813-cw-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.base = self.root / "workspace" / "session-reviews"
        self.findings = self.base / "findings"

    def make_tree(self, keys=(), done_keys=(), rca_keys=(), baseline=None, torn=()):
        self.base.mkdir(parents=True, exist_ok=True)
        for k in keys:
            self.findings.mkdir(parents=True, exist_ok=True)
            (self.findings / (k + ".json")).write_text(json.dumps({"key": k}) + "\n",
                                                       encoding="utf-8")
        for k in done_keys:
            d = self.findings / "done"
            d.mkdir(parents=True, exist_ok=True)
            (d / (k + ".json")).write_text(json.dumps({"key": k}) + "\n", encoding="utf-8")
        lines = [json.dumps(rca_row(k)) for k in rca_keys] + list(torn)
        (self.base / "ledger.jsonl").write_text("".join(l + "\n" for l in lines),
                                                encoding="utf-8")
        if baseline is not None:
            (self.base / "rca-coverage-baseline.json").write_text(
                json.dumps(list(baseline)) + "\n", encoding="utf-8")

    def run_check(self):
        """Call the method under test and record every (status, name, detail) it reports, so the
        assertions are about the verdict and its content, not about how result() prints."""
        checker = cw.Checker(self.root, quick=True)
        recorded = []
        original = checker.result

        def spy(status, name, detail=None):
            recorded.append((status, name, str(detail) if detail is not None else ""))
            return original(status, name, detail)

        checker.result = spy
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            checker.check_rca_coverage()
        return checker, recorded, buf.getvalue()

    def names(self, recorded, status):
        return [name for (st, name, _d) in recorded if st == status]

    def detail_for(self, recorded, name=TITLE):
        return " | ".join(d for (st, n, d) in recorded if n == name)

    def test_an_unanalysed_finding_fails_the_check(self):
        a = key(1)
        self.make_tree(keys=[a])
        checker, recorded, out = self.run_check()
        self.assertEqual([TITLE], self.names(recorded, "FAIL"))
        self.assertIn(a, self.detail_for(recorded),
                      "the report must name the finding key, not just count the gaps")
        self.assertEqual(1, checker.failures)
        self.assertNotIn(TITLE, self.names(recorded, "PASS"))

    def test_a_finding_under_done_counts(self):
        a = key(2)
        self.make_tree(done_keys=[a])
        checker, recorded, out = self.run_check()
        self.assertEqual([TITLE], self.names(recorded, "FAIL"))
        self.assertIn(a, self.detail_for(recorded))

    def test_a_finding_with_an_rca_row_is_clean(self):
        a = key(3)
        self.make_tree(keys=[a], rca_keys=[a])
        checker, recorded, out = self.run_check()
        self.assertEqual([TITLE], self.names(recorded, "PASS"))
        self.assertEqual([], self.names(recorded, "FAIL"))
        self.assertEqual(0, checker.failures)
        self.assertNotIn(a, self.detail_for(recorded), "a covered key must not be reported")

    def test_a_gap_listed_in_the_baseline_is_reported_as_known_not_failed(self):
        a = key(4)
        self.make_tree(keys=[a], baseline=[a])
        checker, recorded, out = self.run_check()
        self.assertEqual([], self.names(recorded, "FAIL"))
        self.assertEqual([TITLE], self.names(recorded, "PASS"))
        self.assertEqual(0, checker.failures)
        self.assertIn("known", self.detail_for(recorded).lower(),
                      "a recorded gap must be named as known, with its count, not silently passed")

    def test_a_gap_that_is_not_in_the_baseline_still_fails(self):
        a = key(5)
        self.make_tree(keys=[a], baseline=[key(99)])
        checker, recorded, out = self.run_check()
        self.assertEqual([TITLE], self.names(recorded, "FAIL"))
        self.assertIn(a, self.detail_for(recorded))

    def test_a_torn_ledger_line_is_not_read_as_clean(self):
        a = key(6)
        self.make_tree(keys=[a], rca_keys=[a],
                       torn=['{"at": "2026-10-08T04:10:59Z", "event": "rca", "key": '])
        checker, recorded, out = self.run_check()
        self.assertEqual([TITLE], self.names(recorded, "FAIL"),
                         "an unreadable ledger must never report clean")
        detail = self.detail_for(recorded)
        self.assertIn("unreadable", detail.lower())
        self.assertIn("1", detail, "say which line is torn (line 1, 1-based)")

    def test_every_gap_is_named_and_nothing_covered_is(self):
        a, b, c = key(7), key(8), key(9)
        covered, baselined = key(10), key(11)
        self.make_tree(keys=[a, b, c, baselined], rca_keys=[covered], baseline=[baselined])
        checker, recorded, out = self.run_check()
        self.assertEqual([TITLE], self.names(recorded, "FAIL"))
        detail = self.detail_for(recorded)
        for k in (a, b, c):
            self.assertIn(k, detail, "every un-accepted gap must be named: " + detail)
        self.assertNotIn(covered, detail)
        self.assertNotIn(baselined, detail,
                         "a key accepted into the baseline is a known gap, not a new one")

    def test_the_check_is_registered_so_session_end_runs_it(self):
        """The defence against 'built, not called' (check_workspace.py:736-739): a check the suite
        does not call protects nothing, and session_end.py commits on this file's exit code."""
        self.assertIn("self.check_rca_coverage", inspect.getsource(cw.Checker.run))


if __name__ == "__main__":
    unittest.main(verbosity=2)
