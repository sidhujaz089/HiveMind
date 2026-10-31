#!/usr/bin/env python3
'''t1050 scope A: turns() must hand the completion channel the tool traffic it documents.

session_candidates.py's completion channel reads a THIRD element per turn. Its own comment
(scripts/session_candidates.py:209-217) states the shape -- ("assistant", text, [event, ...]),
"the events the harness recorded for that reply" -- and tool_events() (:223) reads turns[ti][2]
whenever len(turns[ti]) > 2. Nothing produces that slot: turns() returns (role, text) pairs and is
documented as dropping tool traffic. So discharged_in_reply() (:300) always returns None, dropped()
(:366) never skips, and a promise kept by an Edit in its own reply is minted as a deferral.

That is the t1050 instance. In the real transcript
~/.claude/projects/-home-user-ai-workspace/7e96640c-1d27-4a5f-97b4-50f0594e4ee2.jsonl the
promise sentence (row 1305), the two Edit tool_use blocks on /home/user/ai-workspace/STATE.md
(rows 1306-1307) and their tool_results (rows 1308-1309) are ONE assistant reply: rows 1305-1307
carry the same message.id. The lane reads the sentence and none of the proof, which is why
workspace/rca/72ab8f86f3bf.md minted a deferral for a promise discharged in the same reply.

RED TODAY, for the missing producer and nothing else: test_the_promise_s_reply_carries_its_tool_events
and test_the_events_carry_the_success_text. The other three tests pass on main and must keep
passing -- they pin the contract this change must not disturb: the number of turns, each turn's
role and text, and no fabricated events.

Hermetic. Every transcript is written inside tempfile.TemporaryDirectory(); nothing in the repo is
written; no model call, no subprocess, no network, no live state. The fixtures are synthetic jsonl
rows in the shape read off the real transcript: a top-level "type" ("assistant"/"user"), a
message with id/role/content, content blocks of type "text", "tool_use" (name, input.file_path)
and "tool_result" (tool_use_id, content).

test_the_synthetic_rows_parse_as_turns_today is a fixture-validity test, green on main: it proves
the rows this suite writes are rows today's turns() parses, so a red below is the producer and
never the fixture.
'''
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import session_review  # noqa: E402

NL = chr(10)
APOS = chr(39)
PROMISE = 'I' + APOS + 'll add a dated section on top and update the next step:'
STATE = 'STATE.md'
MSG_ID = 'msg_0507'


def _row(kind, blocks, mid=None):
    msg = {'role': kind, 'content': blocks}
    if mid:
        msg['id'] = mid
    return {'type': kind, 'message': msg}


ASSIST_PROMISE = _row('assistant', [{'type': 'text', 'text': PROMISE}], MSG_ID)
EDIT_1 = _row('assistant', [{'type': 'tool_use', 'id': 'tu_1', 'name': 'Edit',
                             'input': {'file_path': '/home/user/ai-workspace/' + STATE,
                                       'old_string': 'x', 'new_string': 'y'}}], MSG_ID)
EDIT_2 = _row('assistant', [{'type': 'tool_use', 'id': 'tu_2', 'name': 'Edit',
                             'input': {'file_path': '/home/user/ai-workspace/' + STATE,
                                       'old_string': 'a', 'new_string': 'b'}}], MSG_ID)
RESULT_1 = _row('user', [{'type': 'tool_result', 'tool_use_id': 'tu_1',
                          'content': 'The file /home/user/ai-workspace/' + STATE +
                                     ' has been updated successfully.'}])
RESULT_2 = _row('user', [{'type': 'tool_result', 'tool_use_id': 'tu_2',
                          'content': 'The file /home/user/ai-workspace/' + STATE +
                                     ' has been updated successfully.'}])
USER_THANKS = _row('user', [{'type': 'text', 'text': 'thanks'}])

TEXT_ONLY = [ASSIST_PROMISE, USER_THANKS]
REPLY = [ASSIST_PROMISE, EDIT_1, EDIT_2, RESULT_1, RESULT_2, USER_THANKS]


def _write(rows, d):
    path = Path(d) / 'session.jsonl'
    with path.open('w', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=True) + NL)
    return str(path)


def _turns(rows):
    with tempfile.TemporaryDirectory() as d:
        ts = session_review.turns('claude', _write(rows, d))
    assert isinstance(ts, list), 'turns() returned ' + type(ts).__name__ + ', not a list'
    return [list(t) for t in ts]


def _promise_turn(ts):
    hits = [t for t in ts if len(t) > 1 and PROMISE in str(t[1])]
    assert len(hits) == 1, ('expected exactly one turn carrying the promise sentence, found ' +
                            str(len(hits)) + ' in ' + repr([str(t[1])[:60] for t in ts]))
    return hits[0]


def test_the_synthetic_rows_parse_as_turns_today():
    '''Fixture validity, green on main: today's turns() reads these rows and keeps their text.'''
    ts = _turns(TEXT_ONLY)
    assert [t[0] for t in ts] == ['assistant', 'user'], repr(ts)
    assert str(ts[0][1]) == PROMISE, repr(ts[0][1])
    assert str(ts[1][1]) == 'thanks', repr(ts[1][1])


def test_the_tool_rows_add_no_turns():
    '''The contract at scripts/session_review.py:197 (total = len(turns(...))) and every [a:b]
    slice caller: the tool traffic rides ON a turn, it never becomes one, and no text changes.'''
    plain = _turns(TEXT_ONLY)
    ts = _turns(REPLY)
    assert len(ts) == len(plain), ('rows 1306-1309 became turns: ' + str(len(ts)) +
                                   ' vs ' + str(len(plain)) + ' ' + repr([str(t[1])[:40] for t in ts]))
    assert [t[0] for t in ts] == ['assistant', 'user'], repr(ts)
    assert str(ts[0][1]) == PROMISE, repr(ts[0][1])
    assert str(ts[1][1]) == 'thanks', repr(ts[1][1])


def test_the_promise_s_reply_carries_its_tool_events():
    '''RED TODAY: the assistant turn that carries the promise also carries the edits it promised.'''
    ts = _turns(REPLY)
    t = _promise_turn(ts)
    assert t[0] == 'assistant', repr(t[0])
    assert len(t) >= 3, ('the promise\'s turn carries no third slot (len ' + str(len(t)) +
                         '): turns() is producing (role, text) pairs, so tool_events() at '
                         'scripts/session_candidates.py:223 has nothing to read. Read the contract '
                         'at scripts/session_candidates.py:209-217 and the body of turns() at '
                         'scripts/session_review.py:122.')
    events = t[2]
    assert isinstance(events, list) and events, ('the promise\'s third slot is ' + repr(events) +
                                                 '; expected the list of events recorded for that reply')
    assert all(isinstance(e, dict) for e in events), 'events are not dicts: ' + repr(events)
    files = [str(p) for e in events
             for p in (e.get('files_written') if isinstance(e.get('files_written'), (list, tuple)) else [])]
    assert any(f.rstrip('/').endswith(STATE) for f in files), (
        'no event names a file ending in ' + STATE + '; the two Edit blocks in this reply both '
        'name /home/user/ai-workspace/' + STATE + '. events: ' + repr(events))


def test_a_reply_with_no_tool_traffic_records_no_events():
    '''NEGATIVE, green today: a transcript with no tool traffic invents no events for anyone.'''
    ts = _turns(TEXT_ONLY)
    extra = [e for t in ts for e in (t[2] if len(t) > 2 else [])]
    assert extra == [], ('events recorded for a transcript containing no tool traffic: ' + repr(extra))


def test_the_events_carry_the_success_text():
    '''RED TODAY: the record carries the tool_result's own words under output/summary -- the
    fields _about() (scripts/session_candidates.py:292) reads to match a file named in a quote.
    Without them the consumer cannot link a file to a sentence, which is Scope B.'''
    ts = _turns(REPLY)
    t = _promise_turn(ts)
    events = t[2] if len(t) > 2 else []
    blob = ' '.join(str(e.get('output', '')) + ' ' + str(e.get('summary', ''))
                    for e in events if isinstance(e, dict))
    assert STATE in blob, ('no event carries the tool_result text under output/summary; the two '
                          'tool_results in this reply read "The file /home/user/ai-workspace/' +
                          STATE + ' has been updated successfully." events: ' + repr(events))


if __name__ == '__main__':
    _tests = [test_the_synthetic_rows_parse_as_turns_today,
              test_the_tool_rows_add_no_turns,
              test_the_promise_s_reply_carries_its_tool_events,
              test_a_reply_with_no_tool_traffic_records_no_events,
              test_the_events_carry_the_success_text]
    _failed = []
    for _t in _tests:
        try:
            _t()
            print('PASS ' + _t.__name__)
        except AssertionError as _e:
            _failed.append(_t.__name__)
            print('FAIL ' + _t.__name__ + ': ' + str(_e))
        except Exception as _e:
            _failed.append(_t.__name__)
            print('ERROR ' + _t.__name__ + ': ' + type(_e).__name__ + ': ' + str(_e))
    print(str(len(_tests) - len(_failed)) + '/' + str(len(_tests)) + ' passed')
    sys.exit(1 if _failed else 0)
