#!/usr/bin/env python3
'''t1040-free: t1161, the scripted review must not lose audit gold items to the lexical close net.

THE MEASUREMENT (workspace/session-reviews/audit-scores.jsonl, read 2026-10-14): the newest score
row (at 2026-10-14T12:52:38Z, code 1a5ed8b8d3eb) emits 468 candidates where code aa71d1082e6b
(2026-10-13T21:44:08Z) emitted 622, and every count that needs a candidate fell with them --
items right 44 to 34, found 52 to 42, real misses surfaced 15/31 to 12/31, s21 alone 9 right to 6
and 5 real to 2 -- while the graded answer key is identical in both rows (items 128, real 31).
The code between them is the t1039 completion channel (git 63857b7b, merged cb96ec9d, amended
2e70b490), whose only new filter that can fire on the audit path is the lexical one: the scripted
review hands two-slot turns, so tool_events (scripts/session_candidates.py:223-230) returns []
for every turn and discharged_in_reply (:300-309) closes nothing.

closed_in_later_text (scripts/session_candidates.py:312) closes a row on a DONE_WORDS token plus
TWO shared topic words (line 333). Two out of a seven-word quote is 2/7 = 0.29 of its topic,
against the 0.5 share mentioned_later() (:179) was tuned at. Its own docstring (lines 317-318)
says it is the net that works with the embedder off (SEMANTIC = False) -- so it belongs only where
the semantic net is off. This suite pins that, on the shipped SEMANTIC = True, with five
behaviours:

  1. a promise a later DONE_WORDS line shares two topic words with is KEPT (the regression);
  2. a promise the measured word net does close is still DROPPED (no build passes by deleting
     mentioned_later);
  3. with SEMANTIC = False the lexical net still closes case 1 (so the fix GATES
     closed_in_later_text, never deletes it -- t719 narrow lexical net stays alive);
  4. the shipped default keeps the embedder on (no build passes by flipping SEMANTIC);
  5. a promise its own reply discharged with a green test run naming that file is still dropped
     (t1039 completion channel stays wired).

No model call, no live server, nothing written outside a temp dir: the embedder is stubbed in
sys.modules, and best() returning None is the answer mentioned_later() already handles at
scripts/session_candidates.py:198-202.
'''
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import session_candidates as sc  # noqa: E402

# A stand-in embedder that finds nothing: exercises the word channels with no model call and no
# live server, which the oracle rules forbid.
_STUB = types.ModuleType('semantic')
_STUB.RESOLVE_AT = 0.70
_STUB.best = lambda quote, later: None
_STUB.save = lambda: None
sys.modules['semantic'] = _STUB

ASK = 'What is left for the audit tonight?'
# topic_words -> {prune, nightly, sweep, wire, backup, timer, later}: 7 distinct words ('will',
# 'into' and 'the' are in TOPIC_STOP; 'add' and 'nas' are under 4 chars).
PROMISE = 'I will add the NAS prune to the nightly sweep, and wire it into the backup timer, later.'
# topic_words -> {committed, 1f2e3d4, prune, runs, sweep}: shares EXACTLY 2 of the 7 with PROMISE.
# 'committed' is a DONE_WORDS token (session_candidates.py:76), so closed_in_later_text() fires on
# main; the 0.5 word net needs 4 of the 7 and does not.
DONE_NEAR = 'Committed 1f2e3d4: the prune runs in the sweep.'
# topic_words -> {nightly, sweep, prune, wired, timer}: 5 of the quote's 7, so the measured 0.5
# net closes it in every configuration.
CLOSED = 'The nightly sweep prune is wired into the timer.'

FAILS = []


def session(closing):
    '''Jas asks, the assistant promises, a later assistant reply speaks of it in other words.'''
    return [('user', ASK), ('assistant', PROMISE), ('assistant', closing)]


def quotes(rows):
    return [r['quote'] for r in rows]


def test_1_a_later_echo_that_shares_two_words_does_not_close_the_promise():
    '''The regression: the audit gold items live in rows like this one.'''
    rows = sc.dropped(session(DONE_NEAR))
    if PROMISE not in quotes(rows):
        FAILS.append(
            'case 1: the promise was dropped by closed_in_later_text(). A later line sharing 2 of '
            'the quote''s 7 topic words (2/7 = 0.29) is a topic echo, not a discharge; the audit '
            'set lost 154 candidates and 10 gold items this way (44 -> 34 right, 15 -> 12 real '
            'misses surfaced). kept=%r' % (quotes(rows),))
    if len(rows) != 1:
        FAILS.append('case 1: expected exactly one row from a 3-turn session, got %d: %r'
                     % (len(rows), quotes(rows)))


def test_2_the_measured_word_net_still_closes_a_repaid_promise():
    '''Negative assertion: a build that deletes mentioned_later() to pass case 1 fails here.'''
    rows = sc.dropped(session(CLOSED))
    if PROMISE in quotes(rows):
        FAILS.append(
            'case 2: a later sentence sharing 5 of the quote''s 7 topic words must still close it. '
            'The 0.5 word share in mentioned_later() (session_candidates.py:179) is the measured '
            'net and must keep running with the embedder on; the fix gates closed_in_later_text, '
            'it does not remove filters. kept=%r' % (quotes(rows),))


def test_3_the_lexical_net_still_closes_it_when_the_embedder_is_off():
    '''The fix gates closed_in_later_text() on SEMANTIC; it does not delete it.'''
    saved = sc.SEMANTIC
    sc.SEMANTIC = False
    try:
        rows = sc.dropped(session(DONE_NEAR))
    finally:
        sc.SEMANTIC = saved
    if PROMISE in quotes(rows):
        FAILS.append(
            'case 3: with SEMANTIC = False the lexical net must still close the promise -- that is '
            'the configuration closed_in_later_text() names in its own docstring (lines 317-318) '
            'and what keeps t719 narrow lexical net alive on an embedder-less run. kept=%r'
            % (quotes(rows),))
    if sc.SEMANTIC is not saved:
        FAILS.append('case 3: SEMANTIC was not restored by the test harness')


def test_4_the_shipped_default_keeps_the_embedder_on():
    '''Anti-cheat: a build that flips the module global passes case 1 by changing behaviour.'''
    if sc.SEMANTIC is not True:
        FAILS.append(
            'case 4: SEMANTIC must stay True in scripts/session_candidates.py (line 206): the '
            'shipped scripted review runs the embedder. Flipping it off to make case 1 pass '
            'changes the production review, which is not this task.')


def test_5_a_record_in_the_own_reply_still_closes_the_promise():
    '''t1039 completion channel must survive the fix: behaviour, not shape.'''
    ev = {'kind': 'test_run', 'exit_code': 0, 'passed': 136, 'failed': 0,
          'files_written': ['scripts/tests/test_coder_guards.py']}
    promise = 'I will add the guard in test_coder_guards.py so the lane cannot ship it broken.'
    turns = [('user', ASK), ('assistant', promise, [ev])]
    rows = sc.dropped(turns)
    if promise in quotes(rows):
        FAILS.append(
            'case 5: a promise its own reply discharged with a green test run naming that file '
            'must be dropped (t1039, the 23:02:02 / 23:02:11 case). The fix gates the TEXT net on '
            'SEMANTIC; it must not touch the completion channel at session_candidates.py:379-382.')


if __name__ == '__main__':
    for _test in (test_1_a_later_echo_that_shares_two_words_does_not_close_the_promise,
                  test_2_the_measured_word_net_still_closes_a_repaid_promise,
                  test_3_the_lexical_net_still_closes_it_when_the_embedder_is_off,
                  test_4_the_shipped_default_keeps_the_embedder_on,
                  test_5_a_record_in_the_own_reply_still_closes_the_promise):
        _test()
    for _m in FAILS:
        print('FAIL: ' + _m)
    print('FAILURES: %d' % len(FAILS) if FAILS else
          'ok: the lexical close net is a fallback for the embedder-off run; the measured net and '
          'the completion channel are intact')
    sys.exit(1 if FAILS else 0)
