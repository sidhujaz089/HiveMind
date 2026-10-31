#!/usr/bin/env python3
"""Fail-first oracle for t1123: coder.preserve_attempt_bytes must copy applied sandbox
bytes to a destination directory byte-for-byte, so a TESTS-FAILED attempt leaves the
on-disk bytes recoverable (today they are reverted and the sandbox deleted before any
diff against a byte-for-byte oracle's expectation can be taken).

Red on main for the missing change: coder.preserve_attempt_bytes does not exist yet, so
the first call raises AttributeError. Behaviour is asserted (byte-identity, distinct
values kept distinct, return value, source not clobbered, missing rel skipped not
invented), not shape.
"""
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))

import coder  # noqa: E402


def test_preserves_bytes_byte_for_byte():
    root = Path(tempfile.mkdtemp(prefix="t1123-root-"))
    outdir = Path(tempfile.mkdtemp(prefix="t1123-out-"))
    try:
        # Two DISTINCT multi-line bodies with specific trailing-newline structure, so the
        # test cannot pass by collapsing them into one repeated value.
        body_a = "def alpha():\n    return 'alpha'\n\n\ndef beta():\n    return 'beta'\n"
        body_b = "X = 1\nY = 2\n"
        (root / "pkg").mkdir()
        (root / "pkg" / "alpha.py").write_bytes(body_a.encode("utf-8"))
        (root / "b.py").write_bytes(body_b.encode("utf-8"))
        src_a = (root / "pkg" / "alpha.py").read_bytes()
        src_b = (root / "b.py").read_bytes()

        rels = ["pkg/alpha.py", "b.py"]
        preserved = coder.preserve_attempt_bytes(root, rels, outdir)

        # 1. It reports exactly the rels it preserved (value used, not just arity).
        assert list(preserved) == rels, f"returned {list(preserved)!r}, expected {rels!r}"
        # 2. The preserved bytes are byte-identical to the applied sandbox bytes.
        assert (outdir / "pkg" / "alpha.py").read_bytes() == src_a, \
            "preserved bytes for pkg/alpha.py differ from the applied bytes"
        assert (outdir / "b.py").read_bytes() == src_b, \
            "preserved bytes for b.py differ from the applied bytes"
        # 3. Distinct source files preserve DISTINCT values (not one collapsed string).
        assert (outdir / "pkg" / "alpha.py").read_bytes() != (outdir / "b.py").read_bytes(), \
            "two distinct source files were collapsed to one value"
        # 4. Preserving does not clobber the source.
        assert (root / "pkg" / "alpha.py").read_bytes() == body_a.encode("utf-8"), "source a modified"
        assert (root / "b.py").read_bytes() == body_b.encode("utf-8"), "source b modified"
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outdir, ignore_errors=True)


def test_creates_missing_destination_parents():
    # A rel whose destination parent does not yet exist must be created (outdir is fresh).
    root = Path(tempfile.mkdtemp(prefix="t1123-root2-"))
    outdir = Path(tempfile.mkdtemp(prefix="t1123-out2-"))
    try:
        body = "Z = 'z'\n"
        (root / "a" / "c").mkdir(parents=True)
        (root / "a" / "c" / "deep.py").write_bytes(body.encode("utf-8"))
        coder.preserve_attempt_bytes(root, ["a/c/deep.py"], outdir)
        assert (outdir / "a" / "c" / "deep.py").is_file(), "destination was not created"
        assert (outdir / "a" / "c" / "deep.py").read_bytes() == body.encode("utf-8"), \
            "destination bytes differ from the applied bytes"
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outdir, ignore_errors=True)


def test_skips_missing_source_rel_without_inventing():
    # A rel that does not exist in root must be skipped, not fabricated, and reported out.
    root = Path(tempfile.mkdtemp(prefix="t1123-root3-"))
    outdir = Path(tempfile.mkdtemp(prefix="t1123-out3-"))
    try:
        (root / "present.py").write_bytes(b"P = 1\n")
        preserved = coder.preserve_attempt_bytes(root, ["absent.py", "present.py"], outdir)
        assert list(preserved) == ["present.py"], \
            f"expected only the present rel preserved, got {list(preserved)!r}"
        assert not (outdir / "absent.py").exists(), \
            "a missing source rel produced a fabricated destination file"
        assert (outdir / "present.py").read_bytes() == b"P = 1\n", "present rel not preserved"
    finally:
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(outdir, ignore_errors=True)


if __name__ == "__main__":
    failures = 0
    for fn in (test_preserves_bytes_byte_for_byte,
               test_creates_missing_destination_parents,
               test_skips_missing_source_rel_without_inventing):
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"{failures} failure(s)")
    sys.exit(1 if failures else 0)
