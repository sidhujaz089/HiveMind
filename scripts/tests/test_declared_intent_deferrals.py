#!/usr/bin/env python3
"""Oracle for t1098: a user message that enumerates >=2 items must be answered with a
numbered status list, and a `deferred` item must carry a re-check time/condition.

PROVENANCE. workspace/rca/b6909ce088c7.md. On 2026-08-31 Jas's message enumerated five
items; the assistant answered the merge in prose, deferred the 10am timer with the words
"I'll come back to that", and the item was never re-raised in ~2300 turns. The RCA's own
recorder for it (task t1098, checklist item 1) is: "the transcript itself; a session review
greps for multi-item user messages and checks for an adjacent numbered-status response."
This suite is that recorder, made executable.

WHAT IS WRONG TODAY. scripts/check_declared_intent.py has `findings()`, which reads ASSISTANT
turns for first-person declarations and needs a task id near each (its docstring, lines 18-23).
It has no view of the USER side at all, so the exact shape that lost the timer -- a five-item
user message answered by one line of prose -- produces no finding on any surface in this
repository. The function this suite asks for is `deferral_findings(turns)`.

RED ON MAIN FOR ONE REASON: `check_declared_intent` has no `deferral_findings`. Every test
below calls `fn()`, which raises an AssertionError naming that symbol -- the missing change,
not a bug in this file.

THE ORACLE-WEAK DEFENCES BUILT IN. A build that merely greps for "1." passes nothing here:
`test_bare_numbered_list_is_not_a_status_list` requires status WORDS,
`test_partial_status_list_is_flagged` requires the response to cover the ENUMERATED count, and
`test_deferred_item_needs_a_recheck_handle` requires the re-check half of the rule. The
over-fire side (a gate that fires on ordinary prose gets disabled within a week --
AGENTS.md rule 9, and the reason `check_declared_intent.py` is WARN-only) is held by
`test_single_item_user_message_is_not_flagged`,
`test_prose_without_enumeration_markers_is_not_flagged`, and
`test_deferred_with_a_recheck_time_is_silent`.

NO LIVE STATE. Synthetic transcripts only -- no transcript reads, no files, no subprocess, no
model calls. `check_declared_intent` imports `transcripts` lazily inside live_turns and
live_full_texts, so importing the module here touches nothing live.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import check_declared_intent as cdi

FN = "deferral_findings"


def fn():
    """The one symbol this suite exists to add. Missing -> a legible, uniform red."""
    f = getattr(cdi, FN, None)
    if not callable(f):
        raise AssertionError(
            f"{FN}: check_declared_intent has no callable {FN}() -- this function IS the change"
            " the brief asks for (see workspace/rca/b6909ce088c7.md)"
        )
    return f


# The real defect's shape, transcribed from workspace/rca/b6909ce088c7.md: numbered asks,
# prose answer. Distinct item texts on purpose -- a fixture that repeats one placeholder
# string cannot tell "covered every item" from "matched one line" (t471/t297/t370, RCA t613).
USER_MULTI = (
    "1. Remove the Drive references from docs/EXTERNAL-POINTERS.md.\n"
    "2. Leave a note in the outbox for Chat.\n"
    "3. Set a timer for 10am -- that is when my current 5 hour session restarts.\n"
    "4. Merge the vetted branches into main.\n"
)

STATUS_FULL = (
    "1. Remove Drive refs: done\n"
    "2. Outbox note: done\n"
    "3. Timer: deferred -- re-check at the next session start\n"
    "4. Merge branches: done\n"
)

STATUS_TIMER_BARE = (
    "1. Remove Drive refs: done\n"
    "2. Outbox note: done\n"
    "3. Timer: deferred\n"
    "4. Merge branches: done\n"
)

STATUS_TIMER_TIMED = (
    "1. Remove Drive refs: done\n"
    "2. Outbox note: done\n"
    "3. Timer: deferred at 15:00 (next 5-hour boundary)\n"
    "4. Merge branches: done\n"
)

STATUS_PARTIAL = (
    "1. Remove Drive refs: done\n"
    "2. Outbox note: done\n"
)

BARE_NUMBERED = (
    "1. Remove Drive refs\n"
    "2. Outbox note\n"
    "3. Timer\n"
    "4. Merge branches\n"
)

BULLETS = (
    "- Remove the Drive references.\n"
    "- Leave a note in the outbox.\n"
    "- Merge the vetted branches.\n"
)

BULLETS_ANSWERED = (
    "1. Remove Drive refs: done\n"
    "2. Outbox note: deferred -- revisit on 2026-10-20\n"
    "3. Merge branches: done\n"
)


def test_flags_enumerated_user_message_with_no_status_response():
    """THE CASE THE CODE EXISTS FOR. Four numbered asks, one prose answer: a finding."""
    turns = [("user", USER_MULTI),
             ("assistant", "Starting with the merge, since you're blocked on it.")]
    hits = fn()(turns)
    assert len(hits) == 1, f"expected one finding, got {len(hits)}: {hits!r}"
    h = hits[0]
    assert "item" in h.lower(), f"the finding must say items were enumerated: {h!r}"
    assert "4" in h, f"the finding must carry the enumerated count (4): {h!r}"
    assert "turn 0" in h.lower(), f"the finding must locate the user turn for the reader: {h!r}"


def test_numbered_status_list_covering_every_item_is_silent():
    """The compliant response: one numbered line per item, a status on each, the deferred
    item carrying a re-check. Distinct statuses, not one repeated placeholder."""
    assert fn()([("user", USER_MULTI), ("assistant", STATUS_FULL)]) == [], \
        "a complete numbered status list must not be flagged"


def test_partial_status_list_is_flagged():
    """Covering 2 of 4 is the actual failure mode: the merge got answered, the timer did not."""
    hits = fn()([("user", USER_MULTI), ("assistant", STATUS_PARTIAL)])
    assert len(hits) == 1, f"expected one finding for 2-of-4 coverage, got {hits!r}"
    assert "4" in hits[0] and "2" in hits[0], \
        f"the finding must name both counts (4 enumerated, 2 covered): {hits[0]!r}"


def test_bare_numbered_list_is_not_a_status_list():
    """The weak-build killer: a numbered list with NO status word is not a status response."""
    hits = fn()([("user", USER_MULTI), ("assistant", BARE_NUMBERED)])
    assert len(hits) == 1, f"numbered lines with no status word must still be a finding: {hits!r}"


def test_deferred_item_needs_a_recheck_handle():
    """RCA item 2: a bare `deferred` is the same evaporation, dressed as a status.
    The finding must name the item so a reader can act on it."""
    hits = fn()([("user", USER_MULTI), ("assistant", STATUS_TIMER_BARE)])
    assert len(hits) == 1, f"expected one finding for the bare deferral, got {hits!r}"
    h = hits[0]
    assert "deferred" in h.lower(), f"the finding must name the status: {h!r}"
    assert "re-check" in h.lower(), f"the finding must say what is missing: {h!r}"
    assert "Timer" in h, f"the finding must name WHICH item: {h!r}"


def test_deferred_with_a_recheck_time_is_silent():
    """The over-fire guard: honouring the rule must silence it. A build that flags every
    deferral fires on ordinary work and gets disabled within a week (AGENTS.md rule 9)."""
    assert fn()([("user", USER_MULTI), ("assistant", STATUS_TIMER_TIMED)]) == [], \
        "a deferred item carrying a re-check time is compliant"


def test_single_item_user_message_is_not_flagged():
    """Over-fire guard: one item is a request, not a decomposition."""
    turns = [("user", "1. Merge the vetted branches into main.\n"),
             ("assistant", "Starting with the merge.")]
    assert fn()(turns) == [], f"a one-item message must not be flagged: {fn()(turns)!r}"


def test_prose_without_enumeration_markers_is_not_flagged():
    """Over-fire guard: the detector is a marker-shaped check, and the docstring must say so.
    Flagging ordinary prose is how this fixture gets switched off."""
    turns = [("user", "The merge is blocked on the review gate. Also the outbox note is missing.\n"),
             ("assistant", "Looking at the gate now.")]
    assert fn()(turns) == [], f"prose with no enumeration markers must be silent: {fn()(turns)!r}"


def test_bullet_markers_count_as_items():
    """Enumeration is not only 1./2./3. -- bullets are the shape Jas actually types, and a
    build that handles one marker set fails the other."""
    unanswered = fn()([("user", BULLETS), ("assistant", "Doing the merge now.")])
    assert len(unanswered) == 1, f"three bullets answered in prose is the defect: {unanswered!r}"
    assert "3" in unanswered[0], f"three items enumerated: {unanswered[0]!r}"
    answered = fn()([("user", BULLETS), ("assistant", BULLETS_ANSWERED)])
    assert answered == [], f"three items answered with statuses must be silent: {answered!r}"


def test_finding_names_the_user_turn_index():
    """A WARN with no locator is not actionable: the index is the reader's way back to it."""
    turns = [("user", "hello"),
             ("assistant", "hi"),
             ("user", USER_MULTI),
             ("assistant", "Starting with the merge.")]
    hits = fn()(turns)
    assert len(hits) == 1, f"expected one finding, got {hits!r}"
    assert "turn 2" in hits[0].lower(), f"the finding must cite turn 2: {hits[0]!r}"


def test_assistant_only_transcript_is_silent():
    """Nothing to decompose: there is no user message to answer with a status list."""
    assert fn()([("assistant", STATUS_FULL)]) == []
    assert fn()([]) == []


def test_deferrals_in_assistant_prose_are_the_existing_checks_job():
    """The two halves stay disjoint: 'I'll come back to that' with no task id is what
    findings() (check_declared_intent.py:107) already reports. This suite adds the user side,
    not a second copy of the assistant side."""
    turns = [("user", "Merge the vetted branches into main.\n"),
             ("assistant", "I'll come back to the timer later.")]
    assert fn()(turns) == [], f"assistant prose is findings()'s ground, not this one's: {fn()(turns)!r}"


def _main():
    tests = [(n, o) for n, o in sorted(list(globals().items()))
             if n.startswith("test_") and callable(o)]
    failed = []
    for name, f in tests:
        try:
            f()
        except Exception as exc:
            failed.append(name)
            print(f"FAIL {name}: {exc}")
    print(f"{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
