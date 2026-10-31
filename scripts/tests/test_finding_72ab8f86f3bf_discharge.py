#!/usr/bin/env python3
'''t1050: a deferral discharged in the same session is not a deferred finding.

RCA workspace/rca/72ab8f86f3bf.md: session 7e96640c-1d27-4a5f-97b4-50f0594e4ee2, turn 57 of 82,
whose promise -- PROMISE below, quoted verbatim in the task text and in the RCA -- is discharged by
the STATE.md edits in the rows that follow it (the RCA numbers them 1305-1306) and by commit
d2d5ebc3. Nothing in the shipped code asserts that a promise discharged one row later is not a
deferral, so the scripted lane emits the finding anyway. This file pins that instance.

RED TODAY: test_the_discharged_promise_is_not_emitted fails. scripted_items()
(scripts/session_review.py:819) emits one item per group of session_candidates.group(
session_candidates.dropped(ts)), and dropped() keeps an assistant candidate whenever
mentioned_later() finds no later sentence sharing half the quote's topic words -- the promise
sentence shares none with a STATE.md edit. The other three tests pass on main, and that pairing is
the load part: the control proves the lane reads this slice and DOES emit the promise, so the one
red assertion is red for the missing discharge logic, not for a defect in this fixture. It also
refuses a build that goes green by emitting nothing from scripted_items().

The RCA's row numbers are asserted, never assumed: the ground-truth test locates the promise row
inside rows 1300-1310 and fails with the full text of every row in the window when it is not there,
so a rotated transcript is reported, not silently scored.

Hermetic. The transcript is read only; every derived transcript is written inside
tempfile.TemporaryDirectory(); nothing in the repo is written; no model call; no process left
running; the only subprocess is a read-only git show of a commit in this repo, timeout=60.
Four switches, all in the code under test:
  * session_candidates.SEMANTIC = False -- the module's own switch, the same one
    scripts/tests/test_deferral_discharge.py flips. Left on, mentioned_later() imports
    scripts/semantic.py, embeds through the live Ollama server and writes
    workspace/session-reviews/index_cache/vectors.json.
  * sys.modules['session_review_trial'] is a stub whose _semantic_cover raises: scripted_items
    imports it lazily and its own except-Exception path prints a NOTE to stderr and continues, so
    no server is contacted and no failure can be swallowed here.
  * session_review.task_candidates and session_review.commit_candidates are stubbed to return [].
    Both are searches -- one scans workspace/tasks, the other runs git log -S once per emitted item
    -- and they fill only the near/commits/offered fields, which no assertion here reads. Emission
    is decided upstream, at sc.group(sc.dropped(ts)).

No transcript on this box: every test prints SKIP and passes, the pattern t1049 merged with
(test-failures-ceiling.json:589). An absent fixture is reported, never scored as a finding. Point
T1050_TRANSCRIPT at the jsonl to run this against a capture of the session.
'''
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import session_review       # noqa: E402
import session_candidates   # noqa: E402

session_candidates.SEMANTIC = False          # the module's own switch; see the docstring

_trial = types.ModuleType('session_review_trial')


def _no_bridge(*args, **kwargs):
    raise RuntimeError('t1050 oracle: the semantic bridge is switched off (no live server)')


_trial._semantic_cover = _no_bridge
sys.modules['session_review_trial'] = _trial

_probes = []
session_review.task_candidates = lambda probe, limit=5: (_probes.append(probe), [])[1]
session_review.commit_candidates = lambda probe, since, limit=3: (_probes.append(probe), [])[1]

NL = chr(10)
CURLY = chr(0x2019)
APOS = chr(39)

PROMISE = 'I' + APOS + 'll add a dated section on top and update the next step:'
EVIDENCE = 'STATE.md'
COMMIT = 'd2d5ebc3'
FIRST_ROW = 1300
LAST_ROW = 1310
SINCE = '2026-09-01'

TRANSCRIPT = Path(os.environ.get(
    'T1050_TRANSCRIPT',
    str(Path.home() / '.claude/projects/-home-user-ai-workspace'
        / '7e96640c-1d27-4a5f-97b4-50f0594e4ee2.jsonl')))


def _norm(blob):
    '''A curly apostrophe in the transcript must not change which sentence is the promise.'''
    return str(blob).replace(CURLY, APOS)


def _rows():
    '''Rows FIRST_ROW-LAST_ROW of the real transcript, verbatim, as parsed JSON (1-indexed).'''
    out = []
    with TRANSCRIPT.open(encoding='utf-8') as fh:
        for n, line in enumerate(fh, 1):
            if n > LAST_ROW:
                break
            if n >= FIRST_ROW:
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
        raise AssertionError('session_review.turns(' + chr(39) + 'claude' + chr(39) + ', path) '
                             + 'returned ' + type(ts).__name__ + ', not a list; read turns() at '
                             + 'scripts/session_review.py:122 and update this fixture')
    items = session_review.scripted_items(ts, SINCE)
    if not isinstance(items, list):
        raise AssertionError('session_review.scripted_items(ts, SINCE) returned '
                             + type(items).__name__ + ', not a list; read scripted_items() at '
                             + 'scripts/session_review.py:819 and update this fixture')
    return [str(it.get('quote', '')) for it in items]


def _kept(quotes):
    return [q for q in quotes if _norm(PROMISE) in _norm(q)]


def _dump(rows):
    '''The whole text of every row, un-truncated: a failure message is a record.'''
    return NL.join('row ' + str(n) + ' ['
                   + str(r.get('type') or (r.get('message') or {}).get('role') or '?') + ']: '
                   + _row_text(r) for n, r in rows)


def _skip():
    print('SKIP: no transcript at ' + str(TRANSCRIPT)
          + ' (set T1050_TRANSCRIPT to point at one)')


def _locate():
    '''(rows, promise_row, discharge_rows) for this transcript.

    Raises AssertionError carrying the full text of every row in the window when the ground truth
    rca/72ab8f86f3bf.md states is not on disk.
    '''
    rows = _rows()
    assert rows, ('no rows ' + str(FIRST_ROW) + '-' + str(LAST_ROW) + ' in ' + str(TRANSCRIPT)
                  + ': the transcript has fewer than ' + str(LAST_ROW) + ' lines. The RCA numbers '
                  + 'are 1-indexed jsonl line numbers; re-derive them with: grep -n '
                  + chr(39) + 'add a dated section on top' + chr(39) + ' ' + str(TRANSCRIPT))
    hits = [n for n, r in rows if _norm(PROMISE) in _norm(_row_text(r))]
    assert hits, ('no row ' + str(FIRST_ROW) + '-' + str(LAST_ROW) + ' carries the promise quoted '
                  + 'in rca/72ab8f86f3bf.md. Looked for: ' + repr(PROMISE) + '. Re-derive with: '
                  + 'grep -n ' + chr(39) + 'add a dated section on top' + chr(39) + ' '
                  + str(TRANSCRIPT) + '. The window as read, full text of every row:' + NL
                  + _dump(rows))
    promise_row = min(hits)
    dis = [n for n, r in rows if n > promise_row and _norm(EVIDENCE) in _norm(_row_text(r))]
    return rows, promise_row, dis


def test_transcript_carries_the_promise_in_the_window():
    '''Ground truth, green today: the promise the RCA quotes is in the jsonl, rows 1300-1310.'''
    if not TRANSCRIPT.exists():
        _skip()
        return
    rows, promise_row, dis = _locate()
    assert 1300 <= promise_row <= 1310, 'promise located at row ' + str(promise_row)
    assert dis, ('no row after row ' + str(promise_row) + ' in rows ' + str(FIRST_ROW) + '-'
                 + str(LAST_ROW) + ' mentions ' + repr(EVIDENCE) + '; rca/72ab8f86f3bf.md says '
                 + 'rows 1305-1306 carry the STATE.md edits. The window as read, full text of '
                 + 'every row after the promise:' + NL
                 + _dump([r for r in rows if r[0] > promise_row]))
    assert dis[0] - promise_row <= 2, ('the RCA says the promise was discharged ONE jsonl row '
                                       'later (rows 1305-1306); the first row mentioning '
                                       + repr(EVIDENCE) + ' after it is row ' + str(dis[0])
                                       + ', ' + str(dis[0] - promise_row) + ' rows later. If the '
                                       'transcript has rotated, re-derive the rows before trusting '
                                       'any fix: git show ' + COMMIT + ' -- ' + EVIDENCE)


def test_the_discharge_is_a_real_commit_touching_state_md():
    '''Ground truth, green today and after any fix: commit d2d5ebc3 exists and edits STATE.md.

    This is the fixture's evidence chain, not the change: it pins the one number the RCA states
    rather than reads from disk, and it fails loudly if the commit is not in this repo's history.
    '''
    cp = subprocess.run(['git', '-C', str(ROOT), 'show', '--stat', '--format=', COMMIT,
                         '--', EVIDENCE], capture_output=True, text=True, timeout=60)
    assert cp.returncode == 0, ('git show ' + COMMIT + ' failed rc=' + str(cp.returncode)
                                + ': ' + cp.stdout + cp.stderr)
    assert EVIDENCE in cp.stdout, ('commit ' + COMMIT + ' touches no file named ' + repr(EVIDENCE)
                                   + '; git show --stat printed: ' + cp.stdout)


def test_promise_with_no_discharge_in_the_slice_is_emitted():
    '''CONTROL, green today and green after any fix: a promise whose discharge rows are removed
    from the slice IS reported. Without it, a build that emits nothing from scripted_items()
    satisfies the red test by deleting the feature.
    '''
    if not TRANSCRIPT.exists():
        _skip()
        return
    rows, promise_row, dis = _locate()
    first_dis = min(dis)
    slice_rows = [(n, r) for n, r in rows if promise_row <= n < first_dis]
    assert any(n == promise_row for n, _ in slice_rows), ('the control slice must include row '
                                                          + str(promise_row) + ' (the promise)')
    blob = _norm(json.dumps([r for _, r in slice_rows], ensure_ascii=True))
    assert _norm(EVIDENCE) not in blob, ('rows ' + str(promise_row) + '-' + str(first_dis - 1)
                                         + ' already mention ' + repr(EVIDENCE) + ', so the control '
                                         + 'slice proves nothing. Narrow FIRST_ROW/LAST_ROW to the '
                                         + 'promise and its immediate delivery. Rows in the slice '
                                         + 'carrying it: ' + str([n for n, r in slice_rows
                                                                  if _norm(EVIDENCE) in _norm(_row_text(r))]))
    with tempfile.TemporaryDirectory() as d:
        quotes = _emitted(_transcript(slice_rows, d))
    kept = _kept(quotes)
    assert kept, ('the scripted lane did NOT emit the row-' + str(promise_row) + ' promise from a '
                  'slice containing no discharge. It emits every row dropped() keeps; read '
                  'dropped() at scripts/session_candidates.py:206 and mentioned_later() at :177. '
                  'Emitted, whole: ' + repr(quotes))


def test_the_discharged_promise_is_not_emitted():
    '''RED TODAY: the rows after the promise carry the STATE.md edits it promised, so it is not
    deferred work -- and the scripted lane emits it as a finding anyway.
    '''
    if not TRANSCRIPT.exists():
        _skip()
        return
    rows, promise_row, dis = _locate()
    slice_rows = [(n, r) for n, r in rows if promise_row <= n <= max(dis)]
    with tempfile.TemporaryDirectory() as d:
        path = _transcript(slice_rows, d)
        # Non-vacuity: the lane can SEE the discharge in this slice (turns() keeps assistant text
        # blocks). Green below must mean a discharge was recognised, not that the delivery is
        # invisible to the reader.
        seen = _norm(json.dumps(session_review.turns('claude', str(path)), ensure_ascii=True))
        assert _norm(EVIDENCE) in seen, ('turns(' + chr(39) + 'claude' + chr(39) + ', ...) does '
                                        'not surface ' + repr(EVIDENCE) + ' from a slice that '
                                        'contains it: rows ' + str(dis) + ' carry it. Read '
                                        'turns() at scripts/session_review.py:122 before trusting '
                                        'this test.')
        quotes = _emitted(path)
    kept = _kept(quotes)
    assert not kept, ('the scripted lane emits the row-' + str(promise_row) + ' promise as a '
                      'finding even though rows ' + str(dis) + ' of the same session carry the '
                      + EVIDENCE + ' edits it promised: ' + repr(PROMISE) + '. The promise was '
                      'discharged in-session -- see workspace/rca/72ab8f86f3bf.md and commit '
                      + COMMIT + ' -- ' + EVIDENCE + '. Emitted quote(s), whole: ' + repr(kept))


if __name__ == '__main__':
    _tests = [test_transcript_carries_the_promise_in_the_window,
              test_the_discharge_is_a_real_commit_touching_state_md,
              test_promise_with_no_discharge_in_the_slice_is_emitted,
              test_the_discharged_promise_is_not_emitted]
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
            print('ERROR ' + _t.__name__ + ': ' + type(_e).__name__ + ': ' + _e)
    print(str(len(_tests) - len(_failed)) + '/' + str(len(_tests)) + ' passed')
    sys.exit(1 if _failed else 0)
