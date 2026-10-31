#!/usr/bin/env python3
"""The handover guard: session_end must refuse to finish without a handover for TODAY.

WHY (t265). CLAUDE.md claims "session_end.py writes and pushes the handover while the
outgoing session still has full context. The order is what makes it safe." It does not, and
never did -- scripts/session_end.py has never written a workspace/HANDOVER-*.md. Every
handover in that folder was hand-written by a session that happened to remember, so the
safety property the contract describes rested on somebody's attention.

It is worse than a missing file. session_start.py names "the newest workspace/HANDOVER-*.md"
and tells the next session to read it IN FULL. When no handover was written, the next session
is handed a STALE one with nothing saying so -- which happened on 2026-09-11: the newest was
2026-09-09, written at 11:27, while the work it described continued until 16:25.

AUTHORSHIP. Drafted by Q16 (qwen3.8:27b-mtp-bf16) under D-35, 8 min, 0 tool rounds. Reviewed
and amended by claude-code/opus-5, then committed -- that commit is the FREEZE. The
implementation is a separate dispatch and may not edit this file.

Case 3 was rewritten in review; Q16's own summary said it was satisfiable by a constant, and
it was right. See its docstring.
"""

import re
import sys
import tempfile
from pathlib import Path

# Make `scripts/` importable so `import handover` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import handover  # noqa: E402

DAY = "2026-09-11"
STALE_DAY = "2026-09-09"
TARGET = f"workspace/HANDOVER-{DAY}.md"


def _fill(draft_text: str) -> str:
    """Replace every <!-- TODO: ... --> block with real prose."""
    return re.sub(r"<!-- TODO:.*?-->", "Done: completed the task.", draft_text, flags=re.DOTALL)


def _make_root(tmp: Path) -> Path:
    (tmp / "workspace").mkdir(parents=True)
    return tmp


def case_absent(root: Path) -> None:
    """1. No handover file at all → check() names the missing path."""
    problems = handover.check(root, DAY)
    assert isinstance(problems, list), f"expected list, got {type(problems).__name__}"
    assert len(problems) > 0, "check() must be non-empty when the file is absent"
    joined = " | ".join(problems)
    assert TARGET in joined, (
        f"no problem string contains the exact path {TARGET!r}; got: {joined!r}"
    )


def case_skeleton(root: Path) -> None:
    """2. Draft written unchanged → check() still non-empty."""
    draft_text = handover.draft(root, DAY)
    (root / "workspace" / f"HANDOVER-{DAY}.md").write_text(draft_text, encoding="utf-8")
    problems = handover.check(root, DAY)
    assert isinstance(problems, list), f"expected list, got {type(problems).__name__}"
    assert len(problems) > 0, (
        "check() must be non-empty when the file still contains unfilled TODO markers"
    )


def case_filled(root: Path) -> None:
    """3. All TODOs replaced with prose -> check() returns [].

    REWRITTEN BY CLAUDE BEFORE THE FREEZE, and Q16 is the reason. Its own summary flagged
    this case as "Unsatisfiable -- a trivial check()->[] stub passes this by coincidence".
    That is correct and it is failure mode 3 in writing-oracles-for-delegation: an assertion
    loose enough to pass while the thing does nothing.

    The fix is not a tighter assertion, it is a TRANSITION. Write the skeleton to the same
    root and require a complaint, then fill it in place and require silence. A stub that
    always returns [] fails the first half; a stub that always returns ["x"] fails the
    second. Neither constant can pass, which is the property the original case lacked.
    """
    path = root / "workspace" / f"HANDOVER-{DAY}.md"
    draft_text = handover.draft(root, DAY)

    path.write_text(draft_text, encoding="utf-8")
    before = handover.check(root, DAY)
    assert isinstance(before, list), f"expected list, got {type(before).__name__}"
    assert len(before) > 0, (
        "check() answered [] for the unfilled skeleton, so it is not reading the file at all"
    )

    filled = _fill(draft_text)
    assert "<!-- TODO: " not in filled, "internal: _fill did not remove all markers"
    path.write_text(filled, encoding="utf-8")
    after = handover.check(root, DAY)
    assert isinstance(after, list), f"expected list, got {type(after).__name__}"
    assert after == [], f"check() must return [] for a fully-filled handover; got: {after}"


def case_stale(root: Path) -> None:
    """4. Only a different day's filled file exists → check() for today is non-empty."""
    draft_text = handover.draft(root, STALE_DAY)
    filled = _fill(draft_text)
    (root / "workspace" / f"HANDOVER-{STALE_DAY}.md").write_text(filled, encoding="utf-8")
    # No file for DAY
    problems = handover.check(root, DAY)
    assert isinstance(problems, list), f"expected list, got {type(problems).__name__}"
    assert len(problems) > 0, (
        "check() must be non-empty: a stale (different-day) handover must not satisfy today's guard"
    )


def case_draft_has_marker(root: Path) -> None:
    """5. draft() output must literally contain the marker string."""
    draft_text = handover.draft(root, DAY)
    assert "<!-- TODO: " in draft_text, (
        f"draft() must contain the literal marker '<!-- TODO: ' so case 2 is meaningful; "
        f"got: {draft_text[:200]!r}…"
    )


def main() -> int:
    cases = [
        ("absent", case_absent),
        ("skeleton", case_skeleton),
        ("filled", case_filled),
        ("stale", case_stale),
        ("draft_has_marker", case_draft_has_marker),
    ]
    failures = 0
    for name, fn in cases:
        with tempfile.TemporaryDirectory() as td:
            root = _make_root(Path(td))
            try:
                fn(root)
                print(f"[ok] {name}")
            except AssertionError as exc:
                print(f"[BAD] {name}: {exc}")
                failures += 1
            except Exception as exc:
                print(f"[BAD] {name}: unexpected {type(exc).__name__}: {exc}")
                failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
