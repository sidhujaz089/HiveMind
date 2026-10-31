"""t1034: a brief's work identity is its ask, not its bytes.

RCA 3fd304e4d87f: prepending `TASK: t318` to workspace/briefs/brief-p32-scheduler-commands.txt
was administrative metadata. It changed the file's bytes, so dispatch_loop.brief_sha() changed,
and every content-keyed control that reads that hash -- is_parked, futile, review_state --
silently lost the chunk's park, and nothing on disk recorded that the control had been lifted.

Red on main because dispatch_loop has no brief_identity(): every test that calls it raises
AttributeError naming that function. Nothing here touches workspace/briefs -- dispatch_loop.REPO
is pointed at a TemporaryDirectory, the root brief_sha resolves a brief name through.
"""
import importlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

BRIEF = "brief-p32-scheduler-commands.txt"
ASK = (
    "Change scripts/scheduler_commands.py so task_of() resolves a brief's task id from its\n"
    "filename, not from a hand-prepended header line.\n"
    "SYMBOLS: task_of, brief_path\n"
)
PREPEND = "TASK: t318\n"


class TestBriefIdentity(unittest.TestCase):
    def setUp(self):
        self.dl = importlib.import_module("dispatch_loop")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "workspace" / "briefs").mkdir(parents=True)
        self.dl.REPO = self.root

    def _write(self, text):
        (self.root / "workspace" / "briefs" / BRIEF).write_text(text, encoding="utf-8")

    def test_brief_identity_exists(self):
        self.assertTrue(callable(getattr(self.dl, "brief_identity", None)),
                        "dispatch_loop.brief_identity is missing: a brief's key is its raw bytes")

    def test_metadata_prepend_does_not_move_identity(self):
        """THE DEFECT. A prepended `TASK:` header is metadata, not the ask."""
        self._write(ASK)
        before = self.dl.brief_identity(BRIEF)
        self._write(PREPEND + ASK)
        self.assertEqual(before, self.dl.brief_identity(BRIEF),
                         "prepending a TASK: header moved the brief's identity -- that is exactly "
                         "how p32's park was released, with no record")

    def test_a_real_re_brief_moves_identity(self):
        """A substantive edit must move the key: a key nothing can move controls nothing."""
        self._write(ASK)
        before = self.dl.brief_identity(BRIEF)
        self._write(ASK.replace("scheduler_commands.py", "console.py"))
        self.assertNotEqual(before, self.dl.brief_identity(BRIEF),
                            "a substantive re-brief left the identity where it was: the key is inert")

    def test_whitespace_only_change_does_not_move_identity(self):
        """Re-flowing the prose is not a new ask -- and must not release a park."""
        self._write(ASK)
        before = self.dl.brief_identity(BRIEF)
        self._write(ASK.replace("SYMBOLS: task_of, brief_path",
                                "SYMBOLS:    task_of,   brief_path\n\n\n"))
        self.assertEqual(before, self.dl.brief_identity(BRIEF),
                         "identity is not whitespace-normalised: re-flowing text releases parks")

    def test_identity_is_not_the_raw_byte_hash(self):
        """Identity is a PROJECTION of the brief, not a copy of brief_sha()."""
        self._write(ASK)
        self.assertNotEqual(self.dl.brief_identity(BRIEF), self.dl.brief_sha(BRIEF),
                            "brief_identity returned the raw-bytes hash: an incidental byte edit "
                            "still moves the key, which is the defect")

    def test_byte_hash_still_moves_on_any_edit(self):
        """brief_sha() keeps its byte-change job; identity supplements it, never replaces it."""
        self._write(ASK)
        before = self.dl.brief_sha(BRIEF)
        self._write(PREPEND + ASK)
        self.assertNotEqual(before, self.dl.brief_sha(BRIEF), "brief_sha() stopped detecting byte changes")

    def test_identity_is_a_hex_digest_like_the_byte_key(self):
        self._write(ASK)
        key = self.dl.brief_identity(BRIEF)
        self.assertIsInstance(key, str)
        self.assertEqual(len(key), 64)
        int(key, 16)   # ledgers store sha256 hex; a non-hex key breaks every consumer that compares it

    def test_missing_brief_has_no_identity(self):
        self.assertIsNone(self.dl.brief_identity("brief-does-not-exist-anywhere.txt"),
                          "identity of a missing brief must be None, matching brief_sha's OSError path")


if __name__ == "__main__":
    unittest.main(verbosity=2)
