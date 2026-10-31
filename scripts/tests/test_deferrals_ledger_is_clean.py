"""t1275 -- the production deferral ledger carries only real deferrals.

RCA workspace/rca/e6e6a8cfb5f9.md: workspace/deferrals.jsonl is the record built to stop
"I'll write it -- I'm holding it" from being lost. As of 2026-10-16 it is untracked in git
(`git ls-files --error-unmatch` exits 1) and not gitignored, and it holds rows whose
session id is a test fixture -- sess1 (90 rows), t735-oracle-session (101), sid-a/sid-b/
sid-c (30 each), sess-9 (30), sess-1 (29), x1, s1, abc, many -- 350 rows of 894, written by
suites that let record_deferral fall through to its module default (scripts/session_review.py:1185,
:1188 `ledger=DEFERRAL_LEDGER`) instead of passing a tempfile.

RED ON MAIN FOR ONE REASON, not for its own bug: the ledger on disk today contains rows whose
session id is not a real session id, and it is not tracked by git. The change that greens
this file is (a) purging the fixture rows, keeping the real ones, and (b) `git add
workspace/deferrals.jsonl`. This suite is the ratchet: a future test that writes the
production path turns it red again.

The rule is the INVERSION, not a blacklist: real session ids are uuid4, an 8-hex prefix of
one, or `session-<uuid>`. Anything outside that approved set is a finding, so a synthetic id
nobody predicted surfaces the first time it appears.

READ-ONLY. No writes, no temp dirs, no model call, no live server, no process left running.
The one subprocess is `git ls-files`, read-only, timeout=20.
Run: python3 scripts/tests/test_deferrals_ledger_is_clean.py
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # scripts/tests -> repo root
sys.path.insert(0, str(ROOT / "scripts"))
import session_review                               # noqa: E402

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
APPROVED = re.compile(r"^(?:[0-9a-f]{8}|%s|session-%s)$" % (UUID, UUID))

# The sessions this ledger exists to carry. Verified present on 2026-10-16 (180 and 102
# rows). A purge that deletes one of these deletes the record the RCA is about.
KNOWN_REAL = {"745a17ba", "721de5a0"}

PROD_LEDGER = ROOT / "workspace" / "deferrals.jsonl"


def _rows():
    """(rows, unparseable) for every non-blank line. Read-only; never writes."""
    text = PROD_LEDGER.read_text(encoding="utf-8")
    rows, bad = [], []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            bad.append(line)
    return rows, bad


class DeferralsLedgerIntegrity(unittest.TestCase):

    def test_the_ledger_under_test_is_the_production_one(self):
        """Binds this ratchet to the module constant: move DEFERRAL_LEDGER and this goes red."""
        self.assertEqual(str(getattr(session_review, "DEFERRAL_LEDGER", "")), str(PROD_LEDGER),
                         "scripts/session_review.py:1185 no longer points at "
                         "workspace/deferrals.jsonl; this suite and the record it guards "
                         "have silently disconnected")

    def test_the_ledger_is_readable_as_one_row_per_line(self):
        self.assertTrue(PROD_LEDGER.exists(),
                        "workspace/deferrals.jsonl is gone: the deferral record the RCA built "
                        "has to live somewhere")
        rows, bad = _rows()
        self.assertEqual(bad, [],
                         "torn rows belong to test_t1087_deferral_row.py; here a torn row "
                         "hides a session id from this check: %r" % (bad[:2],))
        self.assertTrue(all(isinstance(r, dict) for r in rows),
                        "a deferral row is a JSON object")

    def test_the_ledger_holds_only_real_session_ids(self):
        """RED TODAY: fixture session ids are in the production ledger."""
        rows, _ = _rows()
        offenders = {}
        for row in rows:
            if "_unparsable" in row:
                continue
            sid = row.get("session")
            if not isinstance(sid, str) or not APPROVED.match(sid):
                offenders[sid] = offenders.get(sid, 0) + 1
        self.assertEqual(offenders, {},
                         "%d of %d rows carry a session id that is not a real session id: %r "
                         "-- test fixtures in the production deferral ledger. A ledger that "
                         "mixes fixture rows with real ones is a ratchet that locks bugs in "
                         "and reports them as history (workspace/rca/e6e6a8cfb5f9.md)"
                         % (sum(offenders.values()), len(rows), offenders))

    def test_the_purge_keeps_the_real_rows(self):
        """Negative assertion: the fix removes fixtures, not history."""
        rows, _ = _rows()
        present = {r.get("session") for r in rows}
        missing = KNOWN_REAL - present
        self.assertEqual(missing, set(),
                         "the purge must keep the real deferrals; these sessions are absent: "
                         "%r" % (sorted(missing),))

    def test_the_production_ledger_is_tracked_in_git(self):
        """RED TODAY: git ls-files --error-unmatch exits 1."""
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "workspace/deferrals.jsonl"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0,
                         "workspace/deferrals.jsonl is untracked in git: the record built to "
                         "stop deferrals being lost is itself the kind of record that gets "
                         "lost (git: %r)" % (proc.stdout + proc.stderr,))


if __name__ == "__main__":
    unittest.main(verbosity=2)
