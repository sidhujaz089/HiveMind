#!/usr/bin/env python3
"""t325: the handover check must be keyed on the SESSION, not only on the date.

WHY: scripts/handover.py check() tests only that workspace/HANDOVER-<day>.md exists,
carries no TODO markers, and has a valid ledger (handover.py:120-141). Once ANY
handover for that date exists and is filled, the check is satisfied -- including
one written by an EARLIER session. The second same-day session then closes cleanly
having written nothing, and session_start.py hands the next session somebody else's
handover with nothing marking it stale (session_start.py:426-448). That is t265's
bug (a handover nobody wrote) in a second shape: t265's frozen suite
test_handover_guard.py covers the absent / skeleton / other-DAY shapes, not the
same-day / other-SESSION shape.

THE INCIDENT (t325 note, 2026-09-15): session d962bb7c wrote HANDOVER-2026-09-15.md
at 08:19; session 007b3f63 ran all afternoon and would have passed session_end with
that file untouched. It was handled by appending a second section -- a convention,
not a mechanism. This suite makes that shape a contract.

THE CONTRACT THIS FILE PINS (option B: one file per day, the handover must name
THIS session):
  - handover.draft(root, day, session=None): None or '' -> byte-identical to today;
    non-empty -> same skeleton plus a standalone line 'Session: <session>'.
  - handover.check(root, day, session=None): None or '' -> byte-identical to today
    (the frozen t265 suite pins this); non-empty -> today's checks PLUS a problem
    naming the session when the text lacks the substring 'Session: <session>'.
    Presence is sufficient (the append convention stays legal).

AUTHORSHIP: Manager scoped 2026-09-26 under t325 (top of QUEUE.md). This file is
the FREEZE: the implementation chunk (scripts/handover.py) must not edit this file
(test_handover_guard.py, D-35, sets the precedent).

Red today: four of the seven cases call draft()/check() with a third argument that
does not exist yet -> TypeError. Cases 1, 3, 7 are green today and pin the frozen
two-arg contract so the fix cannot edit the t265 gate into silence (CHECKS.md rule
5).
"""

import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import handover  # noqa: E402

DAY = "2026-09-15"   # the incident date (t325 note)
FIRST = "d962bb7c"    # the earlier session whose filled file is on disk
SECOND = "007b3f63"   # the later session that would have written nothing
TARGET = f"workspace/HANDOVER-{DAY}.md"


def _fill(draft_text: str) -> str:
    """Replace every <!-- TODO: ... --> block with real prose (same shape as the frozen suite)."""
    return re.sub(r"<!-- TODO:.*?-->", "Done: completed the task.", draft_text, flags=re.DOTALL)


def _write(root: Path, text: str) -> None:
    (root / TARGET).write_text(text, encoding="utf-8")


def case_legacy_still_green(root: Path) -> None:
    """1. The frozen t265 contract survives: a filled file, two-arg check() -> []."""
    _write(root, _fill(handover.draft(root, DAY)))
    problems = handover.check(root, DAY)
    assert isinstance(problems, list), f"expected list, got {type(problems).__name__}"
    assert problems == [], f"two-arg check() contract changed; the frozen suite pins it; got: {problems}"


def case_other_session_fails(root: Path) -> None:
    """2. THE t325 BUG: the file on disk is session A's filled handover (no session
    line at all -- exactly what the 09-15 file looked like); session B ends having
    written nothing. check() for B must be NON-EMPTY and must name B."""
    _write(root, _fill(handover.draft(root, DAY)))
    problems = handover.check(root, DAY, SECOND)
    assert isinstance(problems, list), f"expected list, got {type(problems).__name__}"
    assert len(problems) > 0, (
        "check() passed for a session that wrote nothing: the handover on disk "
        "belongs to an earlier session (t325)"
    )
    joined = " | ".join(problems)
    assert SECOND in joined, f"the problem must name the session {SECOND!r}; got: {joined!r}"


def case_this_session_passes(root: Path) -> None:
    """3. The handover names THIS session -> the check is satisfied (filled + ledger + session)."""
    _write(root, _fill(handover.draft(root, DAY)) + f"\nSession: {SECOND}\n")
    problems = handover.check(root, DAY, SECOND)
    assert isinstance(problems, list), f"expected list, got {type(problems).__name__}"
    assert problems == [], f"check() must pass when the handover names this session; got: {problems}"


def case_this_session_unfilled_fails(root: Path) -> None:
    """4. Naming the session must not weaken the TODO gate: the unfilled skeleton still fails."""
    _write(root, handover.draft(root, DAY, SECOND))
    problems = handover.check(root, DAY, SECOND)
    assert isinstance(problems, list), f"expected list, got {type(problems).__name__}"
    assert len(problems) > 0, "check() must stay non-empty for the unfilled skeleton, session or not"


def case_draft_names_session(root: Path) -> None:
    """5. Draft-then-block: the skeleton the author gets shows the shape check() demands."""
    text = handover.draft(root, DAY, SECOND)
    assert f"Session: {SECOND}" in text, (
        f"draft(root, day, session) must contain the line 'Session: {SECOND}' so the gate "
        f"never demands a shape it never showed the author; got: {text[:200]!r}..."
    )


def case_two_sessions_coexist(root: Path) -> None:
    """6. The 09-15 resolution (a second session APPENDS its section) stays legal."""
    text = _fill(handover.draft(root, DAY))
    text += f"\nSession: {FIRST}\n"   # what the earlier session's file names
    text += f"\nSession: {SECOND}\n"  # the append that actually happened
    _write(root, text)
    problems = handover.check(root, DAY, SECOND)
    assert isinstance(problems, list), f"expected list, got {type(problems).__name__}"
    assert problems == [], f"a handover that names both sessions must satisfy the later one; got: {problems}"


def case_empty_session_is_legacy(root: Path) -> None:
    """7. session_end passes '' for human sessions (no CLAUDE_CODE_SESSION_ID): that must
    mean 'legacy day-keyed', not a session named the empty string."""
    _write(root, _fill(handover.draft(root, DAY)))
    problems = handover.check(root, DAY, "")
    assert isinstance(problems, list), f"expected list, got {type(problems).__name__}"
    assert problems == [], f"check(root, day, '') must behave like check(root, day); got: {problems}"


def main() -> int:
    cases = [
        ("1. legacy two-arg contract still green", case_legacy_still_green),
        ("2. other session's handover FAILS this session (t325)", case_other_session_fails),
        ("3. handover naming this session PASSES", case_this_session_passes),
        ("4. unfilled skeleton still fails with session given", case_this_session_unfilled_fails),
        ("5. draft shows the Session: line (draft-then-block)", case_draft_names_session),
        ("6. the append convention (both sessions named) stays legal", case_two_sessions_coexist),
        ("7. empty session id means legacy day-keyed", case_empty_session_is_legacy),
    ]
    failures = 0
    for name, fn in cases:
        with tempfile.TemporaryDirectory(prefix="t325-") as td:
            root = Path(td) / "repo"
            (root / "workspace").mkdir(parents=True)
            try:
                fn(root)
            except AssertionError as exc:
                print(f"[BAD] {name}\n      {exc}")
                failures += 1
            except Exception as exc:  # noqa: BLE001 - a suite must never die with a traceback
                print(f"[BAD] {name}\n      unexpected {type(exc).__name__}: {exc}")
                failures += 1
            else:
                print(f"[ok]  {name}")
    if failures:
        print(f"{failures} of {len(cases)} cases failed")
        return 1
    print(f"all {len(cases)} cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
