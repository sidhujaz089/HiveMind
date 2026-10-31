"""t1034: the end of a park is a recorded act, never a hash mismatch.

RCA 3fd304e4d87f: supervise.is_parked() is a pure key-match read with no counterpart writer, so
a park ends by ceasing to match -- silently, with nothing on disk showing it was lifted, by whom,
or why. On 2026-09-16T01:38:40Z a `sed -i "1i TASK: t318"` on a brief moved the hash and released
p32-scheduler-commands' park.

Red on main for the missing change (write_park_release does not exist; the sha-change test is the
defect). Rows are passed in through the `rows` argument is_parked already accepts, so nothing here
reads or writes workspace/supervision.jsonl; the ledger append is asserted against a
TemporaryDirectory. No model, no server, no subprocess, no fixed /tmp path, no process left running.
"""
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

CHUNK = "p32-scheduler-commands"
OTHER_CHUNK = "p33-console-pin"
SHA_AT_PARK = "6721cef28a92439b4f8e1d3a9c7b2f50618ae4d90c3f7b2a15e8d4093617fa22"
SHA_AFTER = "0000000000000000000000000000000000000000000000000000000000000000"
WHY_RELEASE = "re-briefed: the ask changed"


def _park_row(chunk=CHUNK, sha=SHA_AT_PARK, at="2026-09-16T00:11:29Z"):
    """A PARK row in the shape supervise.is_parked reads today (supervision.jsonl)."""
    return {"at": at, "chunk": chunk, "decision": "PARK", "brief_sha256": sha,
            "by": "claude-code", "why": "DELIVERED AND AWAITING JAS'S MERGE GATE, not failed"}


class TestParkRelease(unittest.TestCase):
    def setUp(self):
        self.sv = importlib.import_module("supervise")

    def test_write_park_release_exists(self):
        self.assertTrue(callable(getattr(self.sv, "write_park_release", None)),
                        "supervise.write_park_release is missing: a park has no way to end on record")

    def test_a_sha_change_alone_does_not_un_park(self):
        """THE DEFECT, as behaviour: the brief's bytes moved; the control stands."""
        self.assertTrue(self.sv.is_parked(CHUNK, SHA_AFTER, rows=[_park_row()]),
                        "a brief edit un-parked the chunk -- the release of p32's park. Ending a "
                        "control has to be a recorded act, not a key mismatch")

    def test_a_matching_key_still_parks(self):
        """Regression guard: the case that works today keeps working (the ledger's rows are byte-keyed)."""
        self.assertTrue(self.sv.is_parked(CHUNK, SHA_AT_PARK, rows=[_park_row()]))

    def test_a_recorded_release_un_parks_the_chunk_under_every_key(self):
        release = self.sv.write_park_release(CHUNK, by="claude-code", why=WHY_RELEASE,
                                            key_before=SHA_AT_PARK, key_after=SHA_AFTER)
        self.assertFalse(self.sv.is_parked(CHUNK, SHA_AFTER, rows=[_park_row(), release]))
        self.assertFalse(self.sv.is_parked(CHUNK, SHA_AT_PARK, rows=[_park_row(), release]),
                         "a release cleared one key of the chunk, not the chunk: a park written "
                         "under the old byte key would still hold the chunk forever")

    def test_release_row_carries_who_why_and_both_keys(self):
        release = self.sv.write_park_release(CHUNK, by="manager", why=WHY_RELEASE,
                                            key_before=SHA_AT_PARK, key_after=SHA_AFTER)
        self.assertIsInstance(release, dict)
        self.assertEqual(release.get("event"), "park-released")
        self.assertEqual(release.get("chunk"), CHUNK)
        self.assertEqual(release.get("by"), "manager")
        self.assertEqual(release.get("reason"), WHY_RELEASE)
        self.assertEqual(release.get("key_before"), SHA_AT_PARK)
        self.assertEqual(release.get("key_after"), SHA_AFTER)
        self.assertTrue(release.get("at"), "no timestamp: the ledger row cannot be ordered against the park")
        json.dumps(release)   # it is appended as one JSON line; a non-serialisable row is not a record

    def test_the_writer_appends_exactly_one_parseable_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / "supervision.jsonl"
            self.sv.write_park_release(CHUNK, by="manager", why=WHY_RELEASE,
                                       key_before=SHA_AT_PARK, key_after=SHA_AFTER,
                                       at="2026-09-16T01:38:40Z", out=str(ledger))
            lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
            self.assertEqual(len(lines), 1, "expected exactly one appended row, got %d" % len(lines))
            row = json.loads(lines[0])
            self.assertEqual(row.get("chunk"), CHUNK)
            self.assertEqual(row.get("event"), "park-released")
            self.assertEqual(row.get("reason"), WHY_RELEASE)
            self.assertEqual(row.get("key_after"), SHA_AFTER)

    def test_a_park_for_another_chunk_does_not_cover_this_one(self):
        self.assertFalse(self.sv.is_parked(OTHER_CHUNK, SHA_AFTER, rows=[_park_row()]))

    def test_a_release_for_another_chunk_does_not_release_this_one(self):
        other = self.sv.write_park_release(OTHER_CHUNK, by="manager", why=WHY_RELEASE,
                                          key_before=SHA_AT_PARK, key_after=SHA_AFTER)
        self.assertTrue(self.sv.is_parked(CHUNK, SHA_AFTER, rows=[_park_row(), other]),
                        "a release naming one chunk lifted the control on a different chunk")

    def test_a_release_dated_before_the_park_does_not_pre_release_it(self):
        """A release clears a park only when it is LATER than it, by the rows' own `at`."""
        early = self.sv.write_park_release(CHUNK, by="manager", why="an older release of an older park",
                                           key_before="a" * 64, key_after="b" * 64,
                                           at="2026-09-15T00:00:00Z")
        self.assertTrue(self.sv.is_parked(CHUNK, SHA_AFTER, rows=[early, _park_row()]),
                        "a release dated BEFORE the park cancelled a control that came after it")

    def test_no_park_row_means_no_control(self):
        self.assertFalse(self.sv.is_parked(CHUNK, SHA_AFTER, rows=[]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
