#!/usr/bin/env python3
"""A line-range (SHAPE B) op must carry the block it replaces, and be refused when the file
under it does not match that block.

WHY (RCA 1e0e465e0ffc; task t1018)
SHAPE B -- {"path","from","to","new"} -- is positional. apply_ops
(projects/local-inference/coder.py, the range branch at lines 1611-1635) checks only
1 <= a <= b <= len(lines) and then writes, so position and count are the entire contract. Any
divergence between the listing shown to the model and the bytes at a..b -- HEAD versus working
tree, a sliced listing read as a whole file, a concurrent lane touching the file mid-run, a
retry after a partial write -- produces a committed, green edit at the WRONG lines. On
2026-08-31 that shipped a delivered file carrying BOTH the new and the old heading, with an
unrelated line destroyed, reported ACCEPTED by terminal, ledger and task note.

This suite pins the schema-level fix: an op may carry the text of the block it replaces, under
the key "expect". When the file's bytes at a..b are not exactly that text, the op is REFUSED --
naming the file, the range and the text actually found there -- and nothing on disk changes.

Red on main because main reads no "expect" key: every op that carries one today is treated as a
plain positional range and the write happens.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

MD = "notes/inbox.md"
PY = "app/sample.py"
MD_ORIG = "# inbox\n\nthis folder is a coding tool.\n"
# Three scratch lines prepended: the divergence shape that produced the 08-31 corruption. The
# model was shown the file WITHOUT them, so naming line 1 is right for what it saw and wrong
# for these bytes.
MD_MUTATED = ("<!-- scratch one -->\n<!-- scratch two -->\n<!-- scratch three -->\n" + MD_ORIG)
PY_ORIG = "def f():\n    return 1\n    return 2\n"
SHOWN_MD_LINE_1 = "# inbox"
SHOWN_PY_BLOCK = "    return 1\n    return 2"
NEW_MD = "# inbox -- Claude Code"
NEW_PY = "    return 0"
NEW_PY_OTHER = "    return 9"
NEW_PY7 = "    return 7"


def setup(root):
    (root / "notes").mkdir(parents=True, exist_ok=True)
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / MD).write_text(MD_ORIG, encoding="utf-8")
    (root / PY).write_text(PY_ORIG, encoding="utf-8")


def op(rel, a, b, new, expect):
    return {"path": rel, "from": a, "to": b, "new": new, "expect": expect}


def test_carried_block_that_does_not_match_is_refused():
    with tempfile.TemporaryDirectory(prefix="range-preimage-") as td:
        root = Path(td)
        setup(root)
        (root / MD).write_text(MD_MUTATED, encoding="utf-8")
        ok, msg = coder.apply_ops(root, [op(MD, 1, 1, NEW_MD, SHOWN_MD_LINE_1)])
        assert ok is False, (
            "the op naming line 1 was applied to a file whose line 1 is a scratch comment: the "
            "heading was kept below and the unrelated line destroyed -- the 2026-08-31 "
            f"corruption, committed and green: {msg!r}")
        assert MD in msg, f"the refusal does not name the file: {msg!r}"
        assert "1-1" in msg, f"the refusal does not name the range 1-1: {msg!r}"
        assert "<!-- scratch one -->" in msg, (
            f"the refusal does not show the text actually found at 1-1: {msg!r}")
        assert (root / MD).read_text(encoding="utf-8") == MD_MUTATED, (
            "the refused op wrote the file it was refused on")


def test_matching_block_applies_as_usual():
    with tempfile.TemporaryDirectory(prefix="range-preimage-") as td:
        root = Path(td)
        setup(root)
        ok, msg = coder.apply_ops(root, [op(MD, 1, 1, NEW_MD, SHOWN_MD_LINE_1)])
        assert ok is True, f"an op whose carried block matches the file was refused: {msg!r}"
        got = (root / MD).read_text(encoding="utf-8")
        assert got == NEW_MD + "\n\nthis folder is a coding tool.\n", (
            f"the accepted op wrote the wrong bytes: {got!r}")


def test_whitespace_in_the_carried_block_is_significant():
    with tempfile.TemporaryDirectory(prefix="range-preimage-") as td:
        root = Path(td)
        setup(root)
        ok, msg = coder.apply_ops(root, [op(PY, 2, 2, NEW_PY_OTHER, "  return 1")])
        assert ok is False, (
            "the carried block differed from line 2 by indentation alone and was applied: "
            f"{msg!r} -- the comparison is exact, not stripped")
        assert (root / PY).read_text(encoding="utf-8") == PY_ORIG, (
            "the refused op rewrote the line it did not match")
        ok, msg = coder.apply_ops(root, [op(PY, 2, 2, NEW_PY_OTHER, "return 1")])
        assert ok is False, (
            "the carried block was the right text with its indentation stripped and was applied: "
            f"{msg!r}")
        assert (root / PY).read_text(encoding="utf-8") == PY_ORIG, (
            "the refused op rewrote the line it did not match")


def test_the_carried_block_covers_the_whole_range():
    with tempfile.TemporaryDirectory(prefix="range-preimage-") as td:
        root = Path(td)
        setup(root)
        # The correct FIRST line of a two-line range is not a match for that range.
        ok, msg = coder.apply_ops(root, [op(PY, 2, 3, "    return 0\n    return 3", "    return 1")])
        assert ok is False, (
            "the carried block covered line 2 of range 2-3 and was applied to 2-3: "
            f"{msg!r} -- the comparison is over the whole range, not its first line")
        assert (root / PY).read_text(encoding="utf-8") == PY_ORIG, (
            "the refused op wrote the file it was refused on")
        ok, msg = coder.apply_ops(root, [op(PY, 2, 3, "    return 0\n    return 3", SHOWN_PY_BLOCK)])
        assert ok is True, (
            f"range 2-3 carrying the whole block it replaces was refused: {msg!r}")
        got = (root / PY).read_text(encoding="utf-8")
        assert got == "def f():\n    return 0\n    return 3\n", (
            f"the accepted op wrote the wrong bytes: {got!r}")


def test_a_refusal_rolls_back_the_whole_batch():
    with tempfile.TemporaryDirectory(prefix="range-preimage-") as td:
        root = Path(td)
        setup(root)
        ops = [op(MD, 1, 1, NEW_MD, SHOWN_MD_LINE_1), op(PY, 2, 3, NEW_PY, "    return 1")]
        ok, msg = coder.apply_ops(root, ops)
        assert ok is False, f"the batch carrying a mismatched block reported success: {msg!r}"
        assert (root / MD).read_text(encoding="utf-8") == MD_ORIG, (
            "the valid op survived a batch that was refused -- a half-applied change")
        assert (root / PY).read_text(encoding="utf-8") == PY_ORIG, (
            "the mismatched op wrote the file it was refused on")


def test_an_op_with_no_preimage_still_applies():
    # Absence is NOT a refusal in this scope: the harness that populates the field is a later
    # build, and refusing a range op that carries no block today would break every existing
    # caller of apply_ops. This test is the guard against a build that refuses everything.
    with tempfile.TemporaryDirectory(prefix="range-preimage-") as td:
        root = Path(td)
        setup(root)
        ok, msg = coder.apply_ops(root, [{"path": PY, "from": 2, "to": 2, "new": NEW_PY7}])
        assert ok is True, f"a range op with no 'expect' key was refused: {msg!r}"
        got = (root / PY).read_text(encoding="utf-8")
        assert got == "def f():\n    return 7\n    return 2\n", (
            f"the accepted op wrote the wrong bytes: {got!r}")


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    bad = 0
    for t in tests:
        try:
            t()
            print(f"  [ok]   {t.__name__}")
        except AssertionError as e:
            bad += 1
            print(f"  [BAD]  {t.__name__}: {e}")
    print(f"test_coder_range_op_carries_its_block: {len(tests) - bad}/{len(tests)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
