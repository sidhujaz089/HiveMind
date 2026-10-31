#!/usr/bin/env python3
'''t1049: a deferral discharged by a command is not a deferred promise.

RCA workspace/rca/7234979a08e2.md, session a61554b0-b446-43f6-bf3f-4c49776b1fa7.

The RCA and the task text number transcript lines from 0. The jsonl is read 1-indexed here, so
their row 402 and row 455 are 1-indexed lines 403 and 456 -- verified on this box, reading the
transcript: line 403 is an assistant text block carrying

    I'll rotate without echoing them here, then give you a command to read them yourself:

and line 456 is an assistant text block handing over exactly the command it promised, inside a
bash fence: cd ~/ai-workspace/scripts && ~/ai-workspace/.venv/bin/python -m workspace_mcp.server
--show-client. Line 402 is an assistant THINKING block and 455 is a user tool_result; turns()
keeps assistant blocks of type text only, so the RCA's numbers cannot be used as-is.

The promise was kept. The scripted lane emits it as a deferral anyway (finding 7234979a08e2):
scripted_items() emits one item per group of session_candidates.group(dropped(ts)), dropped()
(session_candidates.py:206) keeps an assistant candidate when mentioned_later() (:177) finds
nothing, and mentioned_later() needs one later sentence sharing half the quote's topic words. A
command handed over inside a code fence shares none of them. Row 404 pipes the rotation output
through a sed redaction, so the secrets never reach the transcript: the better the hygiene, the
more invisible the promise. This file pins the transcript against that reading.

RED TODAY: test_promise_discharged_by_a_command_is_not_emitted fails. The other two pass, and
that pairing is the load part: the control proves the lane reads this fixture and emits the
promise, so the one red assertion is red for the missing discharge logic, not for a defect here.

Hermetic. The transcript is read only; every derived transcript is written inside
tempfile.TemporaryDirectory(); nothing in the repo is written; no model call; no subprocess
started by this file; nothing left running. Four switches, all in the code under test:
  * session_candidates.SEMANTIC = False -- its own module-level switch (:203). Left true,
    mentioned_later() imports scripts/semantic.py, embeds through the live Ollama server
    (memory.embed, timeout=300) and writes
    workspace/session-reviews/index_cache/vectors.json.
  * sys.modules['session_review_trial'] is a stub whose _semantic_cover raises: scripted_items
    imports it lazily and its own except Exception prints a NOTE to stderr and continues, so no
    server is contacted.
  * session_review.task_candidates and session_review.commit_candidates are stubbed to return [].
    Both are searches -- one scans workspace/tasks, the other runs git log -S -- once per emitted
    item, and they fill only the near/commits/offered fields, which no assertion here reads.
    Emission is decided upstream, at sc.group(sc.dropped(ts)).

No transcript on this box: every test prints SKIP and passes. An absent fixture is reported, never
scored as a finding.
'''
import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import session_review      # noqa: E402
import session_candidates  # noqa: E402

session_candidates.SEMANTIC = False          # the module's own switch; see the docstring

_trial = types.ModuleType('session_review_trial')


def _no_bridge(*args, **kwargs):
    raise RuntimeError('t1049 oracle: the semantic bridge is switched off (no live server)')


_trial._semantic_cover = _no_bridge
sys.modules['session_review_trial'] = _trial

_probes = []
session_review.task_candidates = lambda probe, limit=5: (_probes.append(probe), [])[1]
session_review.commit_candidates = lambda probe, since, limit=3: (_probes.append(probe), [])[1]

NL = chr(10)
CURLY = chr(0x2019)

TRANSCRIPT = Path(os.environ.get(
    'T1049_TRANSCRIPT',
    str(Path.home() / '.claude/projects/-home-user-ai-workspace'
        / 'a61554b0-b446-43f6-bf3f-4c49776b1fa7.jsonl')))

PROMISE = ('I\'ll rotate without echoing them here, then give you a command '
           'to read them yourself:')
DISCHARGE = 'workspace_mcp.server --show-client'
PROMISE_ROW = 403
DISCHARGE_ROW = 456
THREAD_ROWS = range(399, 461)
SINCE = '2026-08-19'


def _norm(blob):
    '''A curly apostrophe in the transcript must not change which sentence is the promise.'''
    return str(blob).replace(CURLY, chr(39))


def _rows(upto=None):
    '''Rows 399-460 of the real transcript, verbatim, as parsed JSON (1-indexed).

    upto (inclusive) truncates the slice: that is how the control test removes the delivery row
    while leaving every other byte of the thread untouched.
    '''
    last = max(THREAD_ROWS)
    out = []
    with TRANSCRIPT.open(encoding='utf-8') as fh:
        for n, line in enumerate(fh, 1):
            if n > last:
                break
            if n in THREAD_ROWS and (upto is None or n <= upto):
                out.append((n, json.loads(line)))
    return out


def _row_text(row):
    '''The human-readable text of one jsonl row, in the shape turns() reads it.'''
    content = (row.get('message') or {}).get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return NL.join(c.get('text', '') for c in content
                       if isinstance(c, dict) and c.get('type') == 'text')
    return ''


def _transcript(rows, directory):
    path = Path(directory) / 'session.jsonl'
    with path.open('w', encoding='utf-8') as fh:
        for _n, row in rows:
            fh.write(json.dumps(row, ensure_ascii=True) + NL)
    return path


def _emitted(path):
    '''The quotes the scripted lane keeps for this transcript -- its claim about the session.'''
    ts = session_review.turns('claude', str(path))
    if not isinstance(ts, list):
        raise AssertionError('session_review.turns(' + chr(39) + 'claude' + chr(39)
                             + ', path) returned ' + type(ts).__name__
                             + ', not a list; read turns() in scripts/session_review.py'
                             + ' and update this fixture')
    items = session_review.scripted_items(ts, SINCE)
    if not isinstance(items, list):
        raise AssertionError('session_review.scripted_items(ts, SINCE) returned '
                             + type(items).__name__ + ', not a list; read scripted_items()'
                             + ' in scripts/session_review.py and update this fixture')
    return [str(it.get('quote', '')) for it in items]


def _kept(quotes):
    return [q for q in quotes if _norm(PROMISE) in _norm(q)]


def _skip():
    print('SKIP: no transcript at ' + str(TRANSCRIPT)
          + ' (set T1049_TRANSCRIPT to point at one)')


def test_transcript_carries_the_promise_and_the_delivery():
    '''Ground truth for the line numbers: if this fails, the RCA's numbers have moved.'''
    if not TRANSCRIPT.exists():
        _skip()
        return
    by_num = dict(_rows())
    assert by_num, ('no rows ' + str(min(THREAD_ROWS)) + '-' + str(max(THREAD_ROWS))
                    + ' in ' + str(TRANSCRIPT) + ': the transcript has fewer than '
                    + str(max(THREAD_ROWS)) + ' lines; the RCA numbers are 0-indexed, add 1')
    assert PROMISE_ROW in by_num, ('no row ' + str(PROMISE_ROW) + ' in the slice; rows read: '
                                   + str(min(by_num)) + '-' + str(max(by_num)))
    assert DISCHARGE_ROW in by_num, ('no row ' + str(DISCHARGE_ROW) + ' in the slice; '
                                     + 'find it with: grep -n -- ' + chr(39) + '--show-client'
                                     + chr(39) + ' ' + str(TRANSCRIPT))
    assert _norm(PROMISE) in _norm(_row_text(by_num[PROMISE_ROW])), (
        'line ' + str(PROMISE_ROW) + ' no longer carries the promise quoted in '
        'rca/7234979a08e2.md. Looked for: ' + repr(PROMISE) + '. Find it with: grep -n '
        + chr(39) + 'rotate without echoing' + chr(39) + ' ' + str(TRANSCRIPT))
    assert _norm(DISCHARGE) in _norm(_row_text(by_num[DISCHARGE_ROW])), (
        'line ' + str(DISCHARGE_ROW) + ' no longer carries the delivery command. Looked for: '
        + repr(DISCHARGE) + '. Find it with: grep -n -- ' + chr(39) + '--show-client' + chr(39)
        + ' ' + str(TRANSCRIPT))


def test_promise_with_no_discharge_is_emitted():
    '''CONTROL, green today and green after any fix: a promise nothing discharges is reported.

    Without it, a build that emits nothing from scripted_items() satisfies the discharge test by
    deleting the feature.
    '''
    if not TRANSCRIPT.exists():
        _skip()
        return
    rows = _rows(upto=DISCHARGE_ROW - 1)
    nums = {n for n, _ in rows}
    assert PROMISE_ROW in nums, ('the slice ending at row ' + str(DISCHARGE_ROW - 1) + ' must '
                                 'include row ' + str(PROMISE_ROW) + ', the promise, or the '
                                 'control proves nothing')
    assert DISCHARGE_ROW not in nums, ('row ' + str(DISCHARGE_ROW) + ' must be OUT of the '
                                       'control slice')
    assert _norm(DISCHARGE) not in _norm(json.dumps([r for _, r in rows], ensure_ascii=True)), (
        'the slice 399-' + str(DISCHARGE_ROW - 1) + ' already contains ' + repr(DISCHARGE)
        + ', so the control proves nothing. Row 398 (the server argparse help, a tool_result) '
        'carries the bare --show-client before the promise; start the slice after it.')
    with tempfile.TemporaryDirectory() as d:
        quotes = _emitted(_transcript(rows, d))
    kept = _kept(quotes)
    assert kept, ('the scripted lane did NOT emit the row-' + str(PROMISE_ROW) + ' promise from a '
                  'slice containing no discharge. It emits every row dropped() keeps; read '
                  'dropped() at scripts/session_candidates.py:206 and mentioned_later() at :177. '
                  'Emitted, whole: ' + repr(quotes))


def test_promise_discharged_by_a_command_is_not_emitted():
    '''RED TODAY: row 456 hands over the promised command, so the promise is not deferred work.'''
    if not TRANSCRIPT.exists():
        _skip()
        return
    rows = _rows()
    assert _norm(DISCHARGE) in _norm(json.dumps([r for _, r in rows], ensure_ascii=True)), (
        'the slice does not contain ' + repr(DISCHARGE) + '; fix DISCHARGE_ROW')
    with tempfile.TemporaryDirectory() as d:
        path = _transcript(rows, d)
        # Non-vacuity: the lane can SEE the discharge in this slice (turns() keeps assistant text
        # blocks). Green below must mean a discharge was recognised, not that the delivery is
        # invisible to the reader.
        seen = _norm(json.dumps(session_review.turns('claude', str(path)), ensure_ascii=True))
        assert _norm(DISCHARGE) in seen, (
            'turns(' + chr(39) + 'claude' + chr(39) + ', ...) does not surface '
            + repr(DISCHARGE) + ' from a slice that contains it: row ' + str(DISCHARGE_ROW)
            + ' is an assistant text block, so it must. Read turns() before trusting this test.')
        quotes = _emitted(path)
    kept = _kept(quotes)
    assert not kept, ('the scripted lane emits the row-' + str(PROMISE_ROW) + ' promise as a '
                     'finding even though row ' + str(DISCHARGE_ROW) + ' of the same session '
                     'hands over the command it promised: ' + repr(DISCHARGE) + '. The promise '
                     'was discharged in-session -- see workspace/rca/7234979a08e2.md. '
                     'Emitted quote(s), whole: ' + repr(kept))


if __name__ == '__main__':
    _tests = [test_transcript_carries_the_promise_and_the_delivery,
              test_promise_with_no_discharge_is_emitted,
              test_promise_discharged_by_a_command_is_not_emitted]
    _failed = []
    for _t in _tests:
        try:
            _t()
            print('PASS ' + _t.__name__)
        except AssertionError as _e:
            _failed.append(_t.__name__)
            print('FAIL ' + _t.__name__ + ': ' + str(_e))
    print(str(len(_tests) - len(_failed)) + '/' + str(len(_tests)) + ' passed')
    sys.exit(1 if _failed else 0)
