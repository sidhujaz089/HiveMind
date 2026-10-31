#!/usr/bin/env python3
# t1139: a deferral discharged IN THE SESSION by a taskstore add/note row is credited with the task
# id, so a session review cannot ship covered_by empty on work the session itself queued.
#
# WHY. workspace/rca/c3ba82c19cc8.md: session 9f8e5d5d, assistant turn 358 of 401, said verbatim
# "I've deliberately not built it -- you asked for the verify script and this deserves your call, not
# my assumption." Four turns later that session ran the task store add for the very work it deferred
# (transcript row 3627: python3 scripts/taskstore.py add --title 'Build the 3-bundle pruning policy'
# --area ops --detail ...) and noted the resulting task t206 (row 3633, per the RCA report -- that
# row was not read by the scoping session). The finding shipped covered_by empty, deferred. The
# reason is structural and is visible in scripts/session_review.py: every cover path _keep uses
# (a tNNN named in the QUOTE whose task is finished, then _archive_cover) compares the quote with
# text OUTSIDE the session -- the task store, the finished-task archive, the commit log. The one
# reader of the session's OWN text, record_promise_ledger, matches the sentence against
# PROMISE_PHRASES and records what it finds as a free-text artefact list on the row, which no cover
# path reads. The discharge is recorded and thrown away. This suite pins the function that stops it.
#
# THE CONTRACT. session_review.promise_discharged_ids(ts, at, window=PROMISE_WINDOW, title=None)
# returns the ordered, de-duplicated task ids the transcript queued for the work deferred at ts[at],
# scanning ts[at+1 : at+1+window] and nothing else. A row counts when its text names
# scripts/taskstore.py, carries a task id tNNN, AND names the deferred title (case-insensitive,
# whitespace-collapsed). A taskstore row about OTHER work credits nothing: a note about a different
# task must not cover this deferral.
#
# THE ROW SHAPES ARE THE REAL ONES. The add row is the invocation the RCA quotes from transcript row
# 3627 (add --title ... --area ops --detail ...); the note row is the `taskstore.py note <id> <text>`
# form. A synthetic phrasing would let the shipped matcher pass on a shape no session emits.
#
# RED ON MAIN FOR THE MISSING CHANGE AND NOTHING ELSE. scripts/session_review.py defines no
# promise_discharged_ids, so tests 1-6 raise AttributeError; record_promise_ledger writes no
# in_session_task_ids key, so test 8 fails on exactly that missing key (asserted by name, not by
# KeyError text). Test 9 is the control: green today and green after any fix, pinning the fact that
# makes this class invisible to the promise ledger -- the finding's own quote is not a
# PROMISE_PHRASES match, so it is the deferral write on _keep (the next scope on this file) that has
# to call the new function.
#
# HERMETIC. The transcript is a plain list argument; the ledger is written inside
# tempfile.TemporaryDirectory(). No repo file is written, no model is called, no subprocess is
# started, nothing is left running. pytest is not installed: _run() runs every test and exits 1.
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import session_review  # noqa: E402

try:
    import session_candidates
    session_candidates.SEMANTIC = False   # the module's own switch; text paths do not embed
except Exception as e:
    print('NOTE: session_candidates SEMANTIC switch not set: ' + type(e).__name__ + ': ' + str(e),
          file=sys.stderr)

TITLE = 'Build the 3-bundle pruning policy'
ID = 't206'
QUOTE = ("I've deliberately not built it -- you asked for the verify script and this deserves "
         'your call, not my assumption. ' + TITLE)
ADD_ROW = ('python3 scripts/taskstore.py add --title ' + repr(TITLE) +
           " --area ops --detail 'Keep only the 3 most recent bundles'")
NOTE_ROW = 'python3 scripts/taskstore.py note t206 ' + repr(
    TITLE + ': keep only the 3 most recent bundles')
OTHER_ROW = 'python3 scripts/taskstore.py note t311 ' + repr('Wire pruning into the nightly run')
PROSE_ROW = "I'll add t206 to the queue later. " + TITLE
FILLER = 'The verify script now descends one level, as agreed.'
TODAY_FIELDS = ('session', 'turn', 'quote', 'promise_phrase', 'matching_actions_within_n_turns',
                'artefact', 'verdict', 'evidence_channel', 'at')


def _ts(promise_at, rows=None):
    """A 14-row transcript of filler, the deferral quote at promise_at, row overrides by index."""
    ts = [('assistant', FILLER) for _ in range(14)]
    ts[promise_at] = ('assistant', QUOTE)
    for idx, text in (rows or {}).items():
        ts[idx] = ('assistant', text)
    return ts


def test_1_a_taskstore_row_naming_the_title_credits_the_id():
    # 1. The real case: the session queues the work it just deferred. Exact value, not membership.
    ids = session_review.promise_discharged_ids(_ts(4, {6: NOTE_ROW}), 4, 6, TITLE)
    assert ids == [ID], 'expected [' + ID + '], got ' + repr(ids)


def test_2_the_window_is_the_bound_it_claims_and_is_not_widened():
    # 2. Row 11 is 7 turns after the deferral at 4: outside window=6, inside window=7. Pinning both
    #    directions is what stops the build hardcoding a length that happens to pass test 1.
    ts = _ts(4, {11: NOTE_ROW})
    assert session_review.promise_discharged_ids(ts, 4, 6, TITLE) == [], (
        'a taskstore row 7 turns after the deferral is outside window=6 and must not be credited')
    assert session_review.promise_discharged_ids(ts, 4, 7, TITLE) == [ID], (
        'window=7 must reach the row 7 turns later -- expected [t206], got '
        + repr(session_review.promise_discharged_ids(ts, 4, 7, TITLE)))


def test_3_the_last_turn_inside_the_window_is_still_credited():
    # 3. at+window-1: the last row the slice covers, so a build that cuts a row short fails here.
    assert session_review.promise_discharged_ids(_ts(4, {9: NOTE_ROW}), 4, 6, TITLE) == [ID], (
        'the turn at at+window-1 is inside the window')


def test_4_a_row_on_the_deferral_turn_itself_is_not_a_discharge():
    # 4. Strictly later. Here the deferral, the add and the note all sit in ONE row, so an
    #    implementation that scans ts[at:] (inclusive) credits the deferral with its own text.
    ts = _ts(4)
    ts[4] = ('assistant', QUOTE + '\n' + ADD_ROW + '\n' + NOTE_ROW)
    assert session_review.promise_discharged_ids(ts, 4, 6, TITLE) == [], (
        'the row that makes the deferral cannot also be the row that discharged it: '
        + repr(session_review.promise_discharged_ids(ts, 4, 6, TITLE)))


def test_5_a_taskstore_row_about_other_work_credits_nothing():
    # 5. M2. A taskstore note naming an id but not the deferred title must not cover this deferral;
    #    a matcher that credits any id within the window passes test 1 and fails here.
    assert session_review.promise_discharged_ids(_ts(4, {6: OTHER_ROW}), 4, 6, TITLE) == [], (
        'a taskstore row about a different task must not cover this deferral: '
        + repr(session_review.promise_discharged_ids(_ts(4, {6: OTHER_ROW}), 4, 6, TITLE)))


def test_6_prose_naming_the_id_and_the_title_is_not_a_discharge():
    # 6. A promise to file it is not a filing: the row names the id AND the title and sits inside the
    #    window, and is still not a taskstore invocation. Prose-only mentions must not manufacture cover.
    assert session_review.promise_discharged_ids(_ts(4, {6: PROSE_ROW}), 4, 6, TITLE) == [], (
        'prose that mentions a task id is not the session queueing the work: '
        + repr(session_review.promise_discharged_ids(_ts(4, {6: PROSE_ROW}), 4, 6, TITLE)))


def test_7_ids_come_in_row_order_and_appear_once():
    # 7. Order and de-duplication, with a same-id repeat and a title-less row in between.
    ts = _ts(0, {1: NOTE_ROW, 2: NOTE_ROW, 3: OTHER_ROW,
                 4: 'python3 scripts/taskstore.py note t210 ' + repr(TITLE)})
    ids = session_review.promise_discharged_ids(ts, 0, 6, TITLE)
    assert ids == [ID, 't210'], 'expected [t206, t210], got ' + repr(ids)


def test_8_record_promise_ledger_records_the_ids_on_the_row():
    # 8. THE VALUE MUST BE USED, NOT JUST RETURNED: the writer has to put it on the row it appends,
    #    because the row is the only record a later gate can consult. Asserted by field name so the
    #    failure says what is missing. Today the key does not exist: red for the missing change.
    promise = "I'll build the 3-bundle pruning policy."
    ts = [('assistant', FILLER), ('assistant', promise), ('assistant', FILLER),
          ('assistant', NOTE_ROW)]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'promise-ledger.jsonl'
        rows = session_review.record_promise_ledger(ts, 't1139-fixture', out_path=out)
        assert out.is_file(), 'record_promise_ledger did not write out_path ' + str(out)
        written = [json.loads(l) for l in out.read_text(encoding='utf-8').splitlines() if l.strip()]
    assert len(rows) == 1, 'expected 1 returned row, got ' + str(len(rows))
    assert len(written) == 1, 'expected 1 line appended to the ledger, got ' + str(len(written))
    row = written[0]
    assert 'in_session_task_ids' in row, (
        'the promise row carries no in_session_task_ids field, so the ids the session queued are '
        'computed and thrown away -- the defect this task exists to close. Keys present: '
        + repr(sorted(row)))
    assert row['in_session_task_ids'] == [ID], (
        'expected [t206], got ' + repr(row['in_session_task_ids']))
    for k in TODAY_FIELDS:
        assert k in row, ('record_promise_ledger no longer writes ' + k + ' -- its own ledger rows '
                          'are a record readers parse; keys present: ' + repr(sorted(row)))


def test_9_the_deferral_quote_is_not_a_promise_phrase_the_ledger_can_see():
    # 9. CONTROL, green today and green after any fix. The promise ledger is driven by
    #    PROMISE_PHRASES; this finding's quote matches none of them, so the promise scan can never
    #    emit a row for a deferral and the new function must also be called at the deferral write
    #    (the next scope, on _keep). If PROMISE_PHRASES ever gains a deferral phrasing this fails,
    #    saying so -- it is a statement about the shipped constant, not a tautology.
    hit = session_review.PROMISE_PHRASES.search(QUOTE)
    assert hit is None, (
        'the finding quote now matches PROMISE_PHRASES (' + repr(hit.group(0)) + '); the promise '
        'ledger reaches deferrals on its own, so the scope note that defers the _keep() gate needs '
        'revisiting')


def _run():
    tests = [test_1_a_taskstore_row_naming_the_title_credits_the_id,
             test_2_the_window_is_the_bound_it_claims_and_is_not_widened,
             test_3_the_last_turn_inside_the_window_is_still_credited,
             test_4_a_row_on_the_deferral_turn_itself_is_not_a_discharge,
             test_5_a_taskstore_row_about_other_work_credits_nothing,
             test_6_prose_naming_the_id_and_the_title_is_not_a_discharge,
             test_7_ids_come_in_row_order_and_appear_once,
             test_8_record_promise_ledger_records_the_ids_on_the_row,
             test_9_the_deferral_quote_is_not_a_promise_phrase_the_ledger_can_see]
    failed = 0
    for t in tests:
        try:
            t()
            print('PASS ' + t.__name__)
        except Exception as e:
            failed += 1
            print('FAIL ' + t.__name__ + ': ' + type(e).__name__ + ': ' + str(e))
    print(str(len(tests) - failed) + '/' + str(len(tests)) + ' passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(_run())
