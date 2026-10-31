#!/usr/bin/env python3
"""Oracle for t1404: a deferral to the handover must resolve to a look-up-able id.

PROVENANCE. workspace/rca/c5395526a0d7.md (session be2121ac, turn 204 of 244, 2026-09-30,
transcript line 3695): "I'll file anything still open in the handover with a task number." The
sentence promised a number and carried none, and the intent-coverage gate session_end.py runs at
session end (session_end.py:856-857) let it pass -- check_declared_intent.py reports every
declaration as a WARN and its main() always returns 0 (the only return on the success path is
line 327), so a deferral phrased as a handover promise evaporates with the session.

RED ON MAIN FOR ONE REASON: scripts/check_declared_intent.py has no `handover_deferral_findings`
symbol, and main() cannot exit non-zero. Tests 1-6 call fn(), which raises an AssertionError
naming the missing symbol; test_main_exits_nonzero_on_an_unnumbered_handover_deferral asserts an
exit code main() cannot produce today.

THE WEAK-BUILD DEFENCES. A build that calls the deferral resolved because the sentence contains
the word "task" fails test_prose_mention_of_a_task_number_is_not_an_id -- the RCA's own sentence
says "with a task number" and has no number in it, which is exactly the prose mention this task
exists to stop. A build that flags every intent fails
test_non_handover_intent_is_not_this_classes_ground, and the exit-code version of the same guard
is test_main_exits_zero_for_a_non_handover_intent: a gate that fires on ordinary prose gets
switched off within a week (AGENTS.md rule 9, and check_declared_intent.py's own docstring
lines 18-23).

NO LIVE STATE. No transcript reads, no subprocess, no model calls, no files written. main()'s
transcript readers live_turns() (line 243) and live_full_texts() (line 277) are replaced for the
duration of the tests that call main() and restored in a finally block, so nothing here reads
this workdir's transcript. The third reader inside main() -- the deferral_findings block at lines
318-324 -- reads the newest transcript on disk and is not patched: the assertions below depend
only on the exit code, which is computed from the patched turns list, never on that block's text.
"""
import io
import sys
from contextlib import contextmanager, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))

import check_declared_intent as cdi

FN = 'handover_deferral_findings'


def fn():
    """The one symbol this suite exists to add. Missing -> a legible, uniform red."""
    f = getattr(cdi, FN, None)
    if not callable(f):
        raise AssertionError(
            f'{FN}: check_declared_intent has no callable {FN}() -- this function IS the change '
            'the brief asks for (see workspace/rca/c5395526a0d7.md)')
    return f


# The verbatim class from the RCA (transcript line 3695) and its resolved forms. The handles are
# DISTINCT real-shaped ids (t475, L-114), not one repeated placeholder, so a build that resolves
# on any substring cannot satisfy every case (t471/t297/t370, RCA t613).
DEFER_UNNUMBERED = "I'll file anything still open in the handover with a task number."
DEFER_WITH_TASK_ID = "I'll file the Flow chart gaps in the handover under t475."
DEFER_WITH_LESSON_ID = "I'll record the truncation rule in the handover, carrying L-114 with it."
NOT_A_HANDOVER_DEFERRAL = "I'll file the gaps in this turn and report it in the past tense."


def test_flags_a_handover_deferral_with_no_id():
    """THE CASE THE CODE EXISTS FOR: the RCA's sentence, verbatim, in an assistant turn."""
    hits = fn()([DEFER_UNNUMBERED])
    assert len(hits) == 1, f'expected one finding, got {len(hits)}: {hits!r}'
    assert 'handover' in hits[0].lower(), f'the finding must name the handover: {hits[0]!r}'


def test_prose_mention_of_a_task_number_is_not_an_id():
    """The defect in one assertion: the sentence says the words "task number" and carries no
    number. Counting the word "task" as a handle is the build this oracle refuses."""
    assert 't475' not in DEFER_UNNUMBERED and 't1404' not in DEFER_UNNUMBERED
    hits = fn()([DEFER_UNNUMBERED])
    assert len(hits) == 1, f'"with a task number" is a prose mention, not a handle: {hits!r}'


def test_a_task_id_resolves_the_deferral():
    assert fn()([DEFER_WITH_TASK_ID]) == [], (
        f'a deferral carrying t475 is resolved: {fn()([DEFER_WITH_TASK_ID])!r}')


def test_a_lesson_id_resolves_the_deferral():
    assert fn()([DEFER_WITH_LESSON_ID]) == [], (
        f'a deferral carrying L-114 is resolved: {fn()([DEFER_WITH_LESSON_ID])!r}')


def test_non_handover_intent_is_not_this_classes_ground():
    """Over-fire guard, function level: an intent that does not name the handover belongs to
    findings() (check_declared_intent.py:131), which keeps reporting it as a WARN. "in this
    turn" is IMMEDIATE (line 93) and "next session" is OFFER (line 59) -- both stay theirs."""
    assert fn()([NOT_A_HANDOVER_DEFERRAL]) == []
    assert fn()(["I'll check the gate next session."]) == []
    assert fn()([DEFER_WITH_TASK_ID.replace('t475', 'the queue')]) == []


def test_finding_locates_the_turn_and_quotes_the_sentence():
    """A finding with no locator is not actionable -- the rule
    test_declared_intent_deferrals.py:190 enforces for its own class: turn index (0-based) plus
    words a reader can search the transcript for."""
    turns = ['hello', 'hi', DEFER_UNNUMBERED]
    hits = fn()(turns)
    assert len(hits) == 1, f'one deferral, one finding: {hits!r}'
    assert 'turn 2' in hits[0].lower(), f'the finding must cite the turn index: {hits[0]!r}'
    assert 'file' in hits[0].lower(), f'the finding must carry the sentence: {hits[0]!r}'


@contextmanager
def driven(turns, full=None):
    """Run main() over a fake session. The two transcript readers are swapped for the call and
    restored in a finally block, so the suite leaves no state behind."""
    full = list(turns) if full is None else list(full)
    orig_t, orig_f = cdi.live_turns, cdi.live_full_texts
    cdi.live_turns = lambda: list(turns)
    cdi.live_full_texts = lambda: list(full)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            rc = cdi.main()
        yield rc, buf.getvalue()
    finally:
        cdi.live_turns, cdi.live_full_texts = orig_t, orig_f


def test_main_exits_nonzero_on_an_unnumbered_handover_deferral():
    """THE DOWNSTREAM BEHAVIOUR. session_end.py section 5c runs this script as a subprocess and
    its blocker count is built from the exit code; a WARN that always exits 0 is a recorder, not
    a gate. The RCA asked for a BLOCKER, so the exit code is what has to change."""
    with driven([DEFER_UNNUMBERED]) as (rc, out):
        assert rc == 1, f'an unnumbered handover deferral must block: rc={rc} out={out!r}'
        assert any('handover' in line.lower() for line in out.splitlines()), (
            f'the recorder must print a legible line naming the handover: {out!r}')


def test_main_exits_zero_when_the_deferral_carries_an_id():
    with driven([DEFER_WITH_TASK_ID, 'Everything is filed under a number.']) as (rc, out):
        assert rc == 0, f't475 resolves the deferral, so the session must pass: rc={rc}'


def test_main_exits_zero_for_a_non_handover_intent():
    """Over-fire guard, exit-code level: ordinary prose stays a WARN. A gate that fires on every
    "I'll" is routed around with --no-verify inside a week (planning-for-sustainment)."""
    with driven([NOT_A_HANDOVER_DEFERRAL, "I'll check the gate next session."]) as (rc, out):
        assert rc == 0, f'only the handover-deferral class blocks: rc={rc}'


def test_main_exits_zero_for_an_empty_session():
    with driven([]) as (rc, out):
        assert rc == 0, f'a session with no turns must pass: rc={rc}'


def _main():
    tests = [(n, o) for n, o in sorted(list(globals().items()))
             if n.startswith('test_') and callable(o)]
    failed = []
    for name, f in tests:
        try:
            f()
        except Exception as exc:
            failed.append(name)
            print(f'FAIL {name}: {exc}')
    print(f'{len(tests) - len(failed)}/{len(tests)} passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_main())
