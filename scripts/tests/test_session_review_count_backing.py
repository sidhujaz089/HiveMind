#!/usr/bin/env python3
"""t1321 -- the reviewer prompt must carry the count-backing requirement.

RCA workspace/rca/263df6eeb9b4.md:38: the reviewer's checklist gains one line --
for each row in any count a review states, name its scope-quality.jsonl outcome +
evidence, or file the row with the Rule 4 command before closing the count.

The reviewer receives the EXTRACT prompt (session_review.py:374). This asserts the
prompt actually carries that requirement -- the shape the existing off-flow / for-jas
oracles already use to pin a report clause (test_session_review_off_flow.py:110).
Red on main today because EXTRACT has no count-backing line yet.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import session_review as sr  # noqa: E402


def test_reviewer_prompt_names_the_ledger():
    """The requirement must point at the real ledger, not a generic word."""
    low = sr.EXTRACT.lower()
    assert "scope-quality.jsonl" in low, (
        "the reviewer prompt (EXTRACT) does not name scope-quality.jsonl -- "
        "a count-backing line that does not cite the ledger is not one")


def test_reviewer_prompt_requires_per_row_disposition():
    """It must demand a disposition for EACH row, with outcome + evidence."""
    low = sr.EXTRACT.lower()
    per_row = ("each row" in low) or ("every row" in low)
    assert per_row, "the reviewer prompt does not require a per-row disposition"
    assert "outcome" in low and "evidence" in low, (
        "the reviewer prompt does not require the row's outcome + evidence")


def _run():
    failures = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as exc:
                failures.append(f"{name}: {exc}")
    if failures:
        for f in failures:
            print("FAIL", f)
        sys.exit(1)
    print("ok")


if __name__ == "__main__":
    _run()
