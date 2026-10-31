#!/usr/bin/env python3
"""t1057: a promise discharged by an in-chat report is not a deferral.

RCA workspace/rca/7780bb2535a7.md, session 721de5a0-effc-479b-bb75-99ca00223ee9
(transcript ~/.claude/projects/-home-user-ai-workspace/721de5a0-effc-479b-bb75-99ca00223ee9.jsonl).

THE PROMISE, quoted verbatim from rca/7780bb2535a7.md line 21 (jsonl:1895):

    I'll bring you the plan and the sustainment section together, with my
    revisions marked.

THE DISCHARGE, quoted from rca/7780bb2535a7.md line 28 (jsonl:2022, 127 rows later,
same session): "Sustainment landed too -- 21.5 KB, and its best idea is a
cross-reference registry". Its artefact is on disk today:
workspace/q16/SUSTAINMENT-from-q16.md, 21,594 bytes (rca line 29).

WHY THE LANE CALLS THIS A DEFERRAL. scripted_items() emits one item per group of
session_candidates.group(dropped(ts)); dropped() keeps an assistant candidate when
mentioned_later() finds nothing, and mentioned_later() needs one later sentence
sharing half the quote's topic words (scripts/tests/test_deferral_discharge.py:18-20
records those functions and their lines). The promise's topic words are plan,
sustainment, section, revisions; the discharge shares ONE of them. The RCA verified
that the promise's distinctive tokens -- bring you, together, revisions marked --
never recur in the transcript. So a phrase-recurrence scan returns negative by
construction and the promise is emitted as deferred work that was in fact delivered.

NO ROW NUMBERS ARE PINNED. The RCA numbers are 0-indexed jsonl (1895, 2022) and this
file reads 1-indexed; the RCA's own 127-row gap is therefore stated in prose only and
asserted here as "discharge comes strictly later, not next door". Both rows are located
by CONTENT, so the suite cannot go red for arithmetic -- only for the missing change.

Hermetic. The transcript is read only; every derived transcript is written inside
tempfile.TemporaryDirectory(); nothing in the repo is written; no model call; no
subprocess started by this file; nothing left running. Four switches, all in the code
under test, copied from scripts/tests/test_deferral_discharge.py:59-73:
  * session_candidates.SEMANTIC = False -- its module-level switch. Left true,
    mentioned_later() imports scripts/semantic.py, embeds through the live Ollama
    server and writes workspace/session-reviews/index_cache/vectors.json.
  * sys.modules['session_review_trial'] is a stub whose _semantic_cover raises: the
    lane imports it lazily and swallows the exception with a NOTE to stderr, so no
    server is contacted.
  * session_review.task_candidates and session_review.commit_candidates are stubbed to
    return []: one scans workspace/tasks, the other runs git log -S per emitted item,
    and both fill fields no assertion here reads. Emission is decided upstream.

No transcript on this box: the three transcript tests print SKIP and pass. An absent
fixture is reported, never scored as a finding.
"""
import json
import os
import re
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
    raise RuntimeError("t1057 oracle: the semantic bridge is switched off (no live server)")


_trial._semantic_cover = _no_bridge
sys.modules['session_review_trial'] = _trial

_probes = []
session_review.task_candidates = lambda probe, limit=5: (_probes.append(probe), [])[1]
session_review.commit_candidates = lambda probe, since, limit=3: (_probes.append(probe), [])[1]

NL = chr(10)

TRANSCRIPT = Path(os.environ.get(
    'T1057_TRANSCRIPT',
    str(Path.home() / '.claude/projects/-home-user-ai-workspace'
        / '721de5a0-effc-479b-bb75-99ca00223ee9.jsonl')))

ARTEFACT = ROOT / 'workspace' / 'q16' / 'SUSTAINMENT-from-q16.md'

PROMISE = ("I'll bring you the plan and the sustainment section together, "
           "with my revisions marked.")
DISCHARGE = 'Sustainment landed too'
SINCE = '2026-09-01'
BEFORE = 40
AFTER = 40
TASK_ID = re.compile(r'\bt\d{3,}\b')


def _norm(blob):
    """A curly apostrophe or an em dash in the transcript must not decide which row is the promise."""
    return (str(blob).replace(chr(0x2019), chr(39))
            .replace(chr(0x2014), '-').replace(chr(0x2013), '-'))


def _row_text(row):
    """The human-readable text of one jsonl row, in the shape turns() reads it."""
    content = (row.get('message') or {}).get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return NL.join(c.get('text', '') for c in content
                       if isinstance(c, dict) and c.get('type') == 'text')
    return ''


def _locate():
    """(promise_rows, discharge_rows, rows-by-number), 1-indexed, rows verbatim, found by CONTENT."""
    promise = []
    discharge = []
    rows = {}
    with TRANSCRIPT.open(encoding='utf-8') as fh:
        for n, line in enumerate(fh, 1):
            row = json.loads(line)
            text = _norm(_row_text(row))
            if _norm(PROMISE) in text:
                promise.append(n)
            if _norm(DISCHARGE) in text:
                discharge.append(n)
            rows[n] = row
    return promise, discharge, rows


def _found():
    """Locate both rows, with an actionable message when the transcript has moved."""
    promise, discharge, rows = _locate()
    assert promise, ('no row of ' + str(TRANSCRIPT) + ' contains ' + repr(PROMISE)
                     + '. Find it with: grep -n ' + chr(39) + 'revisions marked' + chr(39)
                     + ' ' + str(TRANSCRIPT) + '  (the wording is quoted at '
                     + 'workspace/rca/7780bb2535a7.md:21)')
    assert discharge, ('no row contains ' + repr(DISCHARGE) + '. Find it with: grep -n '
                      + chr(39) + 'Sustainment landed' + chr(39) + ' ' + str(TRANSCRIPT))
    return promise, discharge, rows


def _window(rows, first, last):
    return [(n, rows[n]) for n in range(first, last + 1) if n in rows]


def _transcript(pairs, directory):
    path = Path(directory) / 'session.jsonl'
    with path.open('w', encoding='utf-8') as fh:
        for _n, row in pairs:
            fh.write(json.dumps(row, ensure_ascii=True) + NL)
    return path


def _emitted(path):
    """The quotes the scripted lane keeps for this transcript -- its claim about the session."""
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
          + ' (set T1057_TRANSCRIPT to point at one)')


def test_transcript_carries_the_promise_and_the_discharge():
    """Ground truth for the fixture: if this fails, the quoted text has moved, not the code."""
    if not TRANSCRIPT.exists():
        _skip()
        return
    promise, discharge, rows = _found()
    assert len(promise) == 1, ('the promise sentence appears on ' + str(len(promise))
                               + ' rows. workspace/rca/7780bb2535a7.md verified ONE (grep '
                               + chr(39) + 'revisions marked' + chr(39) + ' = 1 hit), so the '
                               + 'fixture text no longer matches this session: ' + str(promise))
    p = promise[0]
    later = [n for n in discharge if n > p]
    assert later, ('every row containing ' + repr(DISCHARGE) + ' comes at or before the promise '
                   'row ' + str(p) + ' (found ' + str(discharge) + '); the RCA puts the '
                   'discharge AFTER the promise (workspace/rca/7780bb2535a7.md:28)')
    d = min(later)
    assert d - p > 1, ('the discharge row ' + str(d) + ' is the row right after the promise row '
                      + str(p) + '. The RCA places it 127 rows later (jsonl:1895 -> jsonl:2022); '
                      'a neighbour leaves no room for the control slice, so the fixture would '
                      'prove nothing.')
    dtext = _norm(_row_text(rows[d]))
    assert not TASK_ID.search(dtext), ('the discharge sentence carries a task id, so a '
                                       'covering-task scan -- not discharge evidence -- could '
                                       'resolve it and this fixture would no longer test the '
                                       'class: ' + repr(_row_text(rows[d]))[:200])


def test_promise_with_no_discharge_is_emitted():
    """CONTROL, green today and green after any fix: a promise nothing discharges is reported."""
    if not TRANSCRIPT.exists():
        _skip()
        return
    promise, discharge, rows = _found()
    p = promise[0]
    d = min(n for n in discharge if n > p)
    pairs = _window(rows, p, d - 1)
    nums = {n for n, _ in pairs}
    assert p in nums, ('the slice ending at the row before the discharge must include row '
                      + str(p) + ', the promise, or the control proves nothing')
    assert d not in nums, ('row ' + str(d) + ' must be OUT of the control slice')
    assert _norm(DISCHARGE) not in _norm(json.dumps([r for _, r in pairs], ensure_ascii=True)), (
        'the control slice already contains ' + repr(DISCHARGE) + ', so it proves nothing; '
        'shrink the slice to end at row ' + str(d - 1))
    with tempfile.TemporaryDirectory() as dirn:
        quotes = _emitted(_transcript(pairs, dirn))
    kept = _kept(quotes)
    assert kept, ('the scripted lane did NOT emit the promise from a slice containing no '
                  'discharge. It emits every row dropped() keeps; read dropped() and '
                  'mentioned_later() in scripts/session_candidates.py. Emitted, whole: '
                  + repr(quotes))


def test_promise_discharged_in_chat_is_not_emitted():
    """RED TODAY: jsonl:2022 reports the promised deliverable, so the promise is not deferred work."""
    if not TRANSCRIPT.exists():
        _skip()
        return
    promise, discharge, rows = _found()
    p = promise[0]
    d = min(n for n in discharge if n > p)
    pairs = _window(rows, max(p - BEFORE, 1), d + AFTER)
    with tempfile.TemporaryDirectory() as dirn:
        path = _transcript(pairs, dirn)
        seen = _norm(json.dumps(session_review.turns('claude', str(path)), ensure_ascii=True))
        assert _norm(DISCHARGE) in seen, ('turns(' + chr(39) + 'claude' + chr(39)
                                         + ', ...) does not surface ' + repr(DISCHARGE)
                                         + ' from a slice that contains it, so a green test below '
                                         'would mean the delivery is invisible to the reader, not '
                                         'that a discharge was recognised. Read turns() in '
                                         'scripts/session_review.py before trusting this test.')
        quotes = _emitted(path)
    kept = _kept(quotes)
    assert not kept, ('the scripted lane emits the promise as a deferral even though the same '
                      'slice contains its discharge, ' + repr(DISCHARGE) + '. The promise was '
                      'discharged in-session: workspace/rca/7780bb2535a7.md (jsonl:1895 promise, '
                      'jsonl:2022 discharge) and workspace/q16/SUSTAINMENT-from-q16.md on disk. '
                      'Emitted quote(s), whole: ' + repr(kept))


def test_the_promised_artefact_is_on_disk():
    """The promise names a deliverable; its artefact is what discharges it, and it exists today."""
    assert ARTEFACT.exists(), ('no ' + str(ARTEFACT) + '. The RCA evidence for the discharge is '
                               'this artefact (workspace/rca/7780bb2535a7.md:29): ls -la '
                               'workspace/q16/')
    assert ARTEFACT.stat().st_size > 1000, ('the group: ' + str(ARTEFACT) + ' is '
                                            + str(ARTEFACT.stat().st_size) + ' bytes. '
                                            'workspace/rca/7780bb2535a7.md:29 records 21,594 B, '
                                            'the 16-minute clean pass. An empty file is not '
                                            'evidence a promise was discharged.')


if __name__ == '__main__':
    _tests = [test_transcript_carries_the_promise_and_the_discharge,
              test_promise_with_no_discharge_is_emitted,
              test_promise_discharged_in_chat_is_not_emitted,
              test_the_promised_artefact_is_on_disk]
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
