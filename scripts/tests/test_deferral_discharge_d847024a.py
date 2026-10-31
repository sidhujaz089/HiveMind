#!/usr/bin/env python3
'''t1420 fail-first oracle: the promise at record 1440 of transcript d847024a is DISCHARGED, not deferred.

Run: python3 scripts/tests/test_deferral_discharge_d847024a.py   (no pytest; exits non-zero on a failure)

RCA workspace/rca/d44edc07fecc.md, finding d44edc07fecc, session
`d847024a-3567-41bc-8a2e-63daa08e3f5d.jsonl` (2,804 records), 2026-10-03. The four records this
finding turns on, read out of that jsonl on this box:

  record 1440  type=assistant  message.id=msg_011Cffi91psLdppM  one block, type 'text':
               'The Models tab loads again (200). The commit was refused, though. I'll find which
               related suite went red:'
  record 1441  type=assistant  message.id=msg_011Cffi91psLdppM  -- THE SAME MESSAGE ID AS 1440.
               one block, type 'tool_use', name Bash, input.command:
               'cd ~/ai-workspace && timeout 900 python3 scripts/related_tests.py 2>&1 | grep -E
               FAIL|red|BAD|...|failed | head -15'
               This settles the RCA's open item D-4: the sentence and the tool call that executes it
               are ONE agent turn split across two records.
  record 1442  type=user  one block, type 'tool_result', content (a string):
               '  related suites: 5 run, 1 red / FAIL  dispatcher: ... / 24 passed, 1 failed'
               -- a RED run: it discharges nothing.
  record 1460  type=user  one block, type 'tool_result', content (a string):
               '25 passed, 0 failed' and '1490dcf9 fix(local-inference): Models tab -- simultaneous
               edits corrupted ROLES.json; lock the read-edit-save'

So the promise was kept 37.76 s after it was said, inside one uninterrupted assistant block, and the
review lane wrote disposition=deferred for it anyway.

WHY IT IS RED TODAY. The channel built for exactly this shape, discharged_in_reply
(scripts/session_candidates.py:300), reads only the events on the promise's OWN turn, and the only
reader of a jsonl transcript, turns() (scripts/session_review.py:122), hands the lane (role, text)
pairs and drops the tool traffic at the door -- the module says so itself in its t1039 comment. There
is no reader that hands the events over. session_candidates has no transcript_turns, so every test
below fails with an AttributeError naming it, and the real transcript's quote is still flagged by
dropped().

Red for the stated reason, not for a bug in this file: the module under test imports cleanly (it
imports re and sys only), every test call is wrapped so a missing attribute is a named FAIL and not a
crash, and tests 1-5 build their own records inside tempfile.mkdtemp, so no assertion depends on a
file outside the repository. Test 6 READS the real transcript; it writes nothing, starts no process,
makes no model call, and prints a loud SKIP with the path if the file is absent.
'''
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import session_candidates as sc    # noqa: E402

sc.SEMANTIC = False    # the module's own switch (:206); True imports semantic and embeds live

SESSION_ID = 'd847024a-3567-41bc-8a2e-63daa08e3f5d'
TRANSCRIPT = (Path.home() / '.claude' / 'projects' / '-home-user-ai-workspace'
              / (SESSION_ID + '.jsonl'))

# The quote as sentences() splits it out of record 1440's text (split regex at session_candidates.py:64).
QUOTE = "I'll find which related suite went red:"

# The two tool results, verbatim from records 1442 and 1460 -- two DISTINCT results in one turn, so a
# build that treats 'a tool result exists' as 'the promise closed' fails here.
RED_TEXT = ('  related suites: 5 run, 1 red\n'
            "  FAIL  dispatcher: the Manager's assignment is an item for the Manager on its model\n"
            '  24 passed, 1 failed')
GREEN_TEXT = ('  NOTE: SERVING.json yielded nothing for <lambda>; using the built-in list.\n'
              '25 passed, 0 failed\n'
              '1490dcf9 fix(local-inference): Models tab -- simultaneous edits corrupted ROLES.json; '
              'lock the read-edit-save')
GREEN_COMMIT = '1490dcf9'
RED_COUNTS = (24, 1)
GREEN_COUNTS = (25, 0)
RED_RECORD = 1442
GREEN_RECORD = 1460

FAILS = []
SKIPS = []


def check(label, cond, detail=''):
    '''Whole values only, never a slice: a failure message is the only record of why an assertion
    failed, and the part that is cut is the part the reader needs.'''
    line = ('PASS  ' if cond else 'FAIL  ') + label
    if not cond and detail:
        line += '\n      ' + detail
    print(line)
    if not cond:
        FAILS.append(label)


def rec(kind, content, mid=None):
    '''One jsonl line in the shape scripts/session_review.py:122 reads: a type, and a message whose
    content is a string (a person speaking) or a list of blocks (text / tool_use / tool_result).
    `mid` is the assistant message id, which is what makes separate records ONE agent turn -- exactly
    what records 1440 and 1441 of the real transcript share.'''
    msg = {'content': content}
    if mid is not None:
        msg['id'] = mid
    return json.dumps({'type': kind, 'message': msg})


def transcript(lines):
    '''Records into a throwaway directory, read back through the reader this task adds.'''
    d = tempfile.mkdtemp(prefix='t1420-')
    path = Path(d) / 'session.jsonl'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return sc.transcript_turns(path)


def synthetic(green=True, green_after_jas=False):
    '''The d847024a shape, built from the records read above: Jas asks, the promise is said, the RED
    run answers it inside the same turn, then the GREEN run carrying the commit. green=False drops the
    green pair; green_after_jas=True moves it after Jas speaks again.'''
    out = [rec('user', 'The models tab will not load. Which related suite went red?'),
           rec('assistant', [{'type': 'text', 'text': QUOTE + ' Let me look.'}], 'msg_1'),
           rec('assistant', [{'type': 'tool_use', 'id': 'tu_1', 'name': 'Bash',
                              'input': {'command': 'timeout 900 python3 scripts/related_tests.py'}}],
               'msg_1'),
           rec('user', [{'type': 'tool_result', 'tool_use_id': 'tu_1',
                         'is_error': False, 'content': RED_TEXT}])]
    if green_after_jas:
        out.append(rec('user', 'ok, close it out'))
    if green:
        mid = 'msg_2' if green_after_jas else 'msg_1'
        out.append(rec('assistant', [{'type': 'tool_use', 'id': 'tu_2', 'name': 'Bash',
                                      'input': {'command': "git commit -m 'lock the read-edit-save'"}}],
                       mid))
        out.append(rec('user', [{'type': 'tool_result', 'tool_use_id': 'tu_2',
                                 'is_error': False, 'content': GREEN_TEXT}]))
    out.append(rec('user', 'ok thanks'))
    return out


def rows_for(ts):
    return [r for r in sc.candidates(ts) if r.get('quote') == QUOTE]


def dropped_quotes(ts):
    return [r.get('quote', '') for r in sc.dropped(ts)]


def t1_the_promise_record_carries_the_events_of_its_own_agent_turn():
    ts = transcript(synthetic())
    rows = rows_for(ts)
    check('the promise sentence is one candidate row of the transcript', len(rows) == 1,
          'rows for the quote: ' + repr(rows))
    if not rows:
        return
    row = rows[0]
    check('the trigger table catches it as PROMISED (it already does: the table is not the defect)',
          row.get('type') == 'PROMISED', 'type read: ' + repr(row.get('type')))
    events = sc.tool_events(ts, row.get('turn'))
    check('the turn carrying the promise TEXT carries its tool events -- across records, not one record',
          len(events) == 2, 'events on the promise turn: ' + repr(events))
    nums = [ev.get('line') for ev in events if isinstance(ev, dict)]
    check('each event is attached at the transcript record it was parsed from',
          nums == [4, 6], 'record numbers: ' + repr(nums))
    codes = [ev.get('exit_code') for ev in events if isinstance(ev, dict)]
    check('each event carries its own outcome, so a red run does not read as a success',
          codes == [1, 0], 'exit codes: ' + repr(codes))


def t2_the_green_tool_result_discharges_the_promise():
    ts = transcript(synthetic())
    rows = rows_for(ts)
    if not rows:
        check('the green tool result closes the promise', False, 'no candidate row for the quote')
        return
    ev = sc.discharged_in_reply(ts, rows[0])
    check('the discharge is a structured event, not a word in a sentence', isinstance(ev, dict),
          'returned: ' + repr(ev))
    if not isinstance(ev, dict):
        return
    check('it is the success: exit_code 0, the field _success requires',
          ev.get('exit_code') == 0, 'exit_code: ' + repr(ev.get('exit_code')))
    check('it parsed the real green count, 25 passed', ev.get('passed') == GREEN_COUNTS[0],
          'passed: ' + repr(ev.get('passed')))
    check('and 0 failed', ev.get('failed') == GREEN_COUNTS[1], 'failed: ' + repr(ev.get('failed')))
    check('its output carries the transcript line itself',
          '25 passed, 0 failed' in str(ev.get('output')), 'output: ' + repr(ev.get('output')))
    check('and the commit that promise produced', GREEN_COMMIT in str(ev.get('output')),
          'output: ' + repr(ev.get('output')))


def t3_the_red_run_on_the_same_turn_is_not_a_discharge():
    ts = transcript(synthetic(green=False))
    rows = rows_for(ts)
    if not rows:
        check('the red run closes nothing', False, 'no candidate row for the quote')
        return
    ev = sc.discharged_in_reply(ts, rows[0])
    check('24 passed, 1 failed closes nothing -- _success is honoured on transcript-parsed events',
          ev is None, 'returned: ' + repr(ev))
    check('so the promise stays flagged when only a red run answered it',
          QUOTE in dropped_quotes(ts), 'dropped: ' + repr(dropped_quotes(ts)))


def t4_a_success_after_the_next_user_turn_is_not_a_discharge():
    ts = transcript(synthetic(green_after_jas=True))
    rows = rows_for(ts)
    if not rows:
        check('a success after Jas speaks closes nothing', False, 'no candidate row for the quote')
        return
    ev = sc.discharged_in_reply(ts, rows[0])
    check('Jas speaking closes the window: the green pair in a later turn discharges nothing',
          ev is None, 'returned: ' + repr(ev))
    check('and the promise stays flagged', QUOTE in dropped_quotes(ts),
          'dropped: ' + repr(dropped_quotes(ts)))


def t5_the_discharged_promise_leaves_the_dropped_list():
    full = transcript(synthetic())
    dq = dropped_quotes(full)
    check('the promise is NOT in the scripted review dropped list', QUOTE not in dq,
          'dropped: ' + repr(dq))
    control = dropped_quotes(transcript(synthetic(green=False)))
    check('and it IS in the dropped list of the same fixture with record 6 removed -- so it is the '
          'success record that removes it, not a build that emits nothing',
          QUOTE in control, 'dropped: ' + repr(control))


def t6_the_real_transcript_is_discharged_by_record_1460():
    if not TRANSCRIPT.is_file():
        why = ('no transcript at ' + str(TRANSCRIPT) + ' -- that file is this suite ground truth '
               '(rca d44edc07fecc); tests 1-5 are the behaviour contract and ran above')
        print('SKIP  ' + why)
        SKIPS.append(why)
        return
    ts = sc.transcript_turns(TRANSCRIPT)
    rows = rows_for(ts)
    check('the promise sentence at record 1440 is a candidate row of the real session',
          len(rows) == 1, 'rows: ' + repr(rows))
    if not rows:
        return
    ev = sc.discharged_in_reply(ts, rows[0])
    check('the real quote is discharged by a structured event', isinstance(ev, dict),
          'returned: ' + repr(ev))
    if not isinstance(ev, dict):
        return
    check('it is the tool success at record 1460, not the red run at 1442',
          ev.get('line') == GREEN_RECORD, 'line: ' + repr(ev.get('line')))
    check('with exit_code 0', ev.get('exit_code') == 0, 'exit_code: ' + repr(ev.get('exit_code')))
    check('the real green count, 25 passed', ev.get('passed') == GREEN_COUNTS[0],
          'passed: ' + repr(ev.get('passed')))
    check('0 failed', ev.get('failed') == GREEN_COUNTS[1], 'failed: ' + repr(ev.get('failed')))
    check('its output carries the transcript own green line',
          '25 passed, 0 failed' in str(ev.get('output')), 'output: ' + repr(ev.get('output')))
    check('and the commit the RCA verified on main', GREEN_COMMIT in str(ev.get('output')),
          'output: ' + repr(ev.get('output')))
    nums = [e.get('line') for e in sc.tool_events(ts, rows[0].get('turn')) if isinstance(e, dict)]
    check('the red run at record 1442 is attached to the same turn, and is not the closer',
          RED_RECORD in nums, 'record numbers attached: ' + repr(nums))
    dq = dropped_quotes(ts)
    check('so the deferral is no longer minted from the real transcript',
          not [q for q in dq if 'related suite' in q],
          'dropped rows naming a related suite: ' + repr([q for q in dq if 'related suite' in q]))


if __name__ == '__main__':
    _tests = [t1_the_promise_record_carries_the_events_of_its_own_agent_turn,
              t2_the_green_tool_result_discharges_the_promise,
              t3_the_red_run_on_the_same_turn_is_not_a_discharge,
              t4_a_success_after_the_next_user_turn_is_not_a_discharge,
              t5_the_discharged_promise_leaves_the_dropped_list,
              t6_the_real_transcript_is_discharged_by_record_1460]
    for _t in _tests:
        try:
            _t()
        except Exception as _e:    # said, never swallowed: the exception IS the red reason
            FAILS.append(_t.__name__)
            print('FAIL  ' + _t.__name__ + ': ' + type(_e).__name__ + ': ' + str(_e))
    print(str(len(_tests) - len(FAILS)) + '/' + str(len(_tests)) + ' passed')
    for _s in SKIPS:
        print('NOTE  ' + _s)
    sys.exit(1 if FAILS else 0)
