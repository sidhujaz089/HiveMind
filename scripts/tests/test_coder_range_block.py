#!/usr/bin/env python3
"""t350 oracle: a line-range edit must show the model the block it is replacing into,
and a BAD-SYNTAX result must change the next attempt's prompt.

Measured (t350:16-19): 4 attempts on a two-line de-slice, all 'unexpected indent'
at line 425; attempts 3 & 4 byte-identical (6064 tok, 89 s). The harness's own note
'A line-range edit replaces whole lines...' (t349:21) is printed, but neither the
error nor the actual file lines enter the next prompt. Sibling: t349, same class,
harness.py line 143.

Idiom copied from test_coder_library_gate.py: a pure function with a pinned
signature that the loop must call, plus a source pin that it is called. Line-range
op shape is {'path','from','to','new'} (test_coder_guards.py:81,114).

Run:  python3 scripts/tests/test_coder_range_block.py
Red today: coder.range_block does not exist and the BAD-SYNTAX branch feeds no
verbatim file lines into the retry, so byte-identical retries (t350:16) persist.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


# A nested target: lines 3-5 are the range a brief would replace; the model must
# be shown these verbatim, at their 8-space depth, plus the enclosing line (2).
BODY = ("def outer():\n"
        "    if cond:\n"
        "        sentinel_zz7 = 1\n"
        "        drop_a = 2\n"
        "        drop_b = 3\n"
        "    return sentinel_zz7\n")


def main():
    report("coder.range_block exists", hasattr(coder, "range_block"), True)
    if not hasattr(coder, "range_block"):
        print(f"\n{PASSED} passed, {FAILED} failed")
        return 1

    print("\nrange_block shows the target's real lines, verbatim:")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "sub").mkdir()
        (root / "sub" / "ind.py").write_text(BODY, encoding="utf-8")
        block = coder.range_block(root, "sub/ind.py", 3, 5)
        report("a valid range returns a block", block is not None, True)
        if isinstance(block, str):
            report("  the range's lines are present with EXACT leading whitespace",
                   "        sentinel_zz7 = 1" in block and "        drop_b = 3" in block, True)
            report("  the enclosing line above the range is shown (the depth to match)",
                   "    if cond:" in block, True)
            report("  lines carry the file's real line numbers (sliced idiom)",
                   f"{3:5d}  " in block, True)
        report("a range past the end is refused with None (apply_ops idiom)",
               coder.range_block(root, "sub/ind.py", 3, 99), None)
        report("a file that is not there is refused with None (sliced idiom)",
               coder.range_block(root, "sub/nope.py", 1, 2), None)

    print("\nThe BAD-SYNTAX branch of the loop feeds it into the retry prompt:")
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    report("the BAD-SYNTAX branch exists", src.find("BAD-SYNTAX") > 0, True)
    # def line contains 'range_block(root,' too, so >= 2 occurrences means def + call
    # (the library_gate pin shape, test_coder_library_gate.py:66-70).
    report("  and the loop calls range_block (def + call)",
           src.count("range_block(root,") >= 2, True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
