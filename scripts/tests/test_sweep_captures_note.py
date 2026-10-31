#!/usr/bin/env python3
"""A swept capture must not be recorded as untracked. (t1294)

D-38 (2026-10-13) made chat/ TRACKED -- .gitignore:117-122, "chat/ IS TRACKED since D-38
(2026-10-13) -- it used to be ignored here (D-22)" -- and `git ls-files chat/` returns 310
files. scripts/sweep_captures.py:124-125 still prints, on every successful sweep, "These are
in chat/, which is gitignored under D-22 -- so they are still ONE copy until
scripts/backup_corpora.py runs against the USB." That is a record stating a premise the
repository's own .gitignore has reversed, which is the status-document-currency-drift class.

The assertions below are behavioural, not shape: they run the real main() against temp dirs
(no live state touched), confirm the sweep still copies byte-for-byte and removes the source
only after a verified copy, and then read what it REPORTS -- the record a future session will
trust. The three banned strings are the false claim in its own words; "backup_corpora" is
required so the note cannot be satisfied by deleting it.
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "sweep_captures.py"


def load_module():
    spec = importlib.util.spec_from_file_location("sweep_captures", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SweepRecordIsTrueTest(unittest.TestCase):
    """main() against temp dirs only: LIVE/DEST are module constants read from __file__, so
    the test rebinds them on the loaded module rather than touching the repository."""

    def setUp(self):
        self.mod = load_module()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.live = root / "chat-live"
        self.live.mkdir()
        self.dest = root / "captures"
        self.mod.LIVE = self.live
        self.mod.DEST = self.dest
        self.name = "2026-10-18__thinkpad-ssh-environment.jsonl"
        self.body = json.dumps({"session": "s1", "role": "user", "text": "hello"}) + "\n"
        (self.live / self.name).write_text(self.body, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _main(self, argv):
        out = io.StringIO()
        old = sys.argv
        sys.argv = ["sweep_captures.py"] + argv
        try:
            with contextlib.redirect_stdout(out):
                rc = self.mod.main()
        finally:
            sys.argv = old
        return rc, out.getvalue()

    def test_apply_sweeps_and_the_record_it_prints_is_true(self):
        src = self.live / self.name
        rc, out = self._main(["--apply"])
        self.assertEqual(rc, 0, f"--apply reported a failure:\n{out}")
        # The capture reached the corpus, byte for byte -- the guarantee the file exists for.
        landed = self.dest / self.name
        self.assertTrue(landed.is_file(), f"not copied into the corpus:\n{out}")
        self.assertEqual(landed.read_text(encoding="utf-8"), self.body)
        # The source is removed only because the copy was verified -- never before.
        self.assertFalse(src.exists(), "source removed without a verified copy")
        # The RECORD must not claim the corpus is outside git: D-38 tracked chat/.
        for false_claim in ("gitignored", "D-22", "ONE copy"):
            self.assertNotIn(
                false_claim, out,
                f"the sweep record still asserts {false_claim!r}, which D-38 reversed; "
                f"output was:\n{out}")
        # And it must still name the second-copy mechanism that IS true, so a fix that deletes
        # the note wholesale does not pass.
        self.assertIn("backup_corpora", out,
                      f"the record no longer says where the second copy comes from:\n{out}")

    def test_report_mode_touches_nothing_and_makes_the_false_claim(self):
        src = self.live / self.name
        rc, out = self._main([])
        self.assertEqual(rc, 0, f"report mode should exit 0:\n{out}")
        self.assertTrue(src.exists(), "report mode deleted the source")
        self.assertFalse(self.dest.exists(), f"report mode created the corpus:\n{out}")
        for false_claim in ("gitignored", "D-22", "ONE copy"):
            self.assertNotIn(false_claim, out,
                             f"report mode asserts {false_claim!r}:\n{out}")


if __name__ == "__main__":
    RESULT = unittest.main(exit=False, verbosity=2).result
    sys.exit(0 if RESULT.wasSuccessful() else 1)
