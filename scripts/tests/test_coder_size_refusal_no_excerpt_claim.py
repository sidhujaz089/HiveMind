#!/usr/bin/env python3
"""t979 oracle: coder.py's size refusal must not claim an excerpt the model never got.

Dispatch p308-t805-run-tests (swift, 2026-10-08T17:43:32Z) asked for
scripts/run_tests.py; coder.py refused with 'too large to show beside the prompt
(~8782 tokens with it, window 262144). Work from the excerpt you already have',
but that file was never shown -- the branch that emits it is reached only when the
file is absent from `opened`. The gate is correct; the message is a lie.

This oracle pins the TRUTH the message must state (and that the LIE is gone),
and guards the two things that must NOT change: the already-opened (slice) refusal
at the 'rel in opened' branch, and the 'too large to show beside the prompt'
prefix that test_coder_symbol_slice.py pins within 1200 chars of 'body = excerpt(rel)'.

Hermetic: reads coder.py as text. No import of coder, no model, no subprocess,
no writes, no fixed /tmp paths.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "projects" / "local-inference" / "coder.py").read_text(
    encoding="utf-8", errors="replace")

FAILS = []


def ck(name, cond, detail=""):
    print(f"  [{'ok' if cond else 'BAD'}]  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def test_the_lie_is_gone():
    """The false claim must not appear anywhere in coder.py."""
    LIE = "Work from the excerpt you already have"
    ck("the LIE is not in coder.py", LIE not in SRC,
       "the refusal still claims an excerpt exists for a file never shown (t979)")


def test_the_refusal_says_the_file_was_not_shown():
    """The size-refusal message must state the truth and point at the one real source."""
    ck("the refusal says the model has NOT been shown the file",
       "NOT been shown" in SRC,
       "the refusal does not state that the file was never shown (t979)")
    ck("the refusal points the builder to the brief (the source it does have)",
       "Work from the brief" in SRC,
       "the refusal does not direct the builder to the brief as the available source")
    ck("the refusal tells the builder to record that the file was unavailable",
       "not available to you" in SRC,
       "the refusal does not ask the builder to say the file was unavailable")


def test_the_slice_branch_is_untouched():
    """The already-opened (slice) refusal is accurate there and must be preserved."""
    SLICE = "no room for more of it beside the prompt"
    ck("the slice-branch refusal keeps its accurate wording", SLICE in SRC,
       f"{SLICE!r} missing -- that message is true where the file WAS opened, must stay")


def test_the_size_gate_prefix_is_unchanged():
    """The 'too large to show beside the prompt' prefix stays at the call site.
    (test_coder_symbol_slice.py checks for it within 1200 chars of 'body = excerpt(rel)').
    t979 is about the text of the message, not removing the size gate."""
    ck("the size-refusal prefix is still present",
       "too large to show beside the prompt" in SRC,
       "the size-refusal prefix was removed entirely (t979 fixes the text, not the gate)")


if __name__ == "__main__":
    for fn in (test_the_lie_is_gone,
               test_the_refusal_says_the_file_was_not_shown,
               test_the_slice_branch_is_untouched,
               test_the_size_gate_prefix_is_unchanged):
        print(f"\n{fn.__name__}:")
        fn()
    print(f"\n{len(FAILS)} failed" + (": " + ", ".join(FAILS) if FAILS else ""))
    sys.exit(1 if FAILS else 0)
