#!/usr/bin/env python3
'''t1540: a finished ask_q16 answer must reach disk even when its job directory was swept.

Evidence (the two unusable attempts of session-review finding ee9e07071821, 2026-10-20):
  workspace/run-logs/dispatch/ee9e07071821.json--rca--swift--1792536428.log
  workspace/run-logs/dispatch/ee9e07071821.json--rca--swift--1792539118.log
Each shows the model answering in full -- '-> done_reason=stop 30 min 19086 chars
(11 tool round(s))' and '39 min 26484 chars (11 tool round(s))' -- and then the same crash,
quoted from the log and confirmed against the file on 2026-10-21:

  File ".../scripts/ask_q16.py", line 603, in main
    out = write_answer(a.out, r)
  File ".../scripts/ask_q16.py", line 517, in write_answer
    o.write_text(r["text"])
  FileNotFoundError: [Errno 2] No such file or directory:
    '/home/user/.cache/hivemind-tmp/session-review-jb2v4c2r/answer.md'

The answer existed. The write died. scripts/session_review.py:320 makes the job dir with
tempfile.mkdtemp, which lands in the shared ~/.cache/hivemind-tmp (scripts/dispatch_loop.py
JOB_TMP) and is swept while a 30-39 minute RCA is still running, so by the time write_answer
reaches :517 the parent is gone. session_review.py:517,1528 then recorded 'no answer (timed
out or empty)' -- a record that says something that did not happen -- and the item went to
workspace/session-reviews/failed/.

This is the third dispatch to lose a finished answer the same way: scripts/tests/
test_ask_q16_write_answer_survives_swept_dir.py (t1522) and ..._v2.py (t1534) are already
red on main for it, so this file adds only what neither pins: this incident's ~19k-char
answer written whole (no slicing), and the empty-answer sidecar landing in a swept directory.

RED on main for ONE reason -- the write statements raise FileNotFoundError when the parent of
--out is gone. Tests 1-4 exercise the swept shape; test 5 is a neighbour guard that passes
on main. Imports ask_q16 from ROOT/scripts (parents[2]), uses no model, no server, no
process, and writes only inside tempfile.mkdtemp.
'''
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

IMPORT_ERROR = ''
ask_q16 = None
try:
    import ask_q16
except Exception as _exc:  # named in every failure message, never swallowed
    IMPORT_ERROR = 'cannot import scripts/ask_q16.py: %s: %s' % (type(_exc).__name__, _exc)

# Distinct real strings throughout: one repeated placeholder cannot tell the answer from
# the thinking from a prior answer (oracle-weak). ~19k chars, the size this incident made.
BODY = ''.join(
    'line %03d: stop the runner and its direct children using the pids in /proc.\n' % i
    for i in range(320)) + 'TAIL-MARKER-t1540-9f31\n'
THINKING = 'the reasoning the 11 tool rounds cost: the job directory is gone, recreate it.\n'
PRIOR = 'PRIOR-ANSWER-SENTINEL-4471\n'


def _require_module():
    if IMPORT_ERROR:
        raise AssertionError(IMPORT_ERROR)


def test_1_the_finished_answer_lands_when_the_job_dir_is_gone():
    '''The exact crash: an --out whose parent directory no longer exists.'''
    _require_module()
    assert len(BODY) > 19000, 'the fixture shrank below the 19086 chars this incident produced'
    d = Path(tempfile.mkdtemp(prefix='t1540-a-'))
    try:
        out = d / 'session-review-jb2v4c2r' / 'answer.md'
        assert not out.parent.exists(), 'the fixture must start swept, as it was on 2026-10-20'
        got = ask_q16.write_answer(str(out), {'text': BODY, 'thinking': THINKING,
                                              'done_reason': 'stop', 'elapsed_s': 1800})
        assert out.is_file(), (
            'write_answer threw away a finished answer: it returned %r and there is no file '
            'at the --out path it was given (%s). The directory had been swept and the write '
            'at ask_q16.py:517 refused to recreate it -- this is how the 19086-char answer of '
            'ee9e07071821 was lost.' % (got, out))
        assert str(out) == got, 'write_answer must return the path it wrote: %r' % (got,)
        on_disk = out.read_text(encoding='utf-8')
        assert on_disk == BODY, ('the answer was not written whole: %d of %d characters '
                                 'reached the file' % (len(on_disk), len(BODY)))
        assert on_disk.endswith('TAIL-MARKER-t1540-9f31\n'), 'the tail of the answer is missing'
        assert out.parent.is_dir(), 'the job dir was not recreated, so the next writer into it '\
                                    'loses its record the same way'
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_2_the_record_says_stop_and_not_no_answer():
    '''The .meta.json row is the durable record; it must say what happened.'''
    _require_module()
    d = Path(tempfile.mkdtemp(prefix='t1540-b-'))
    try:
        out = d / 'session-review-wirc56v3' / 'answer.md'
        ask_q16.write_answer(str(out), {'text': BODY, 'thinking': THINKING,
                                        'done_reason': 'stop', 'elapsed_s': 2340})
        meta = Path(str(out) + '.meta.json')
        assert meta.is_file(), 'no .meta.json beside the answer: the run left no record'
        row = json.loads(meta.read_text(encoding='utf-8'))
        assert row.get('done_reason') == 'stop', \
            'the record says %r, not stop: the parent would report no answer' % (row.get('done_reason'),)
        assert 'text' not in row, 'the meta row must not duplicate the answer text: %r' % (sorted(row),)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_3_the_reasoning_is_kept_when_the_parent_is_swept():
    '''ask_q16 keeps the reasoning always (2026-09-23, seven runs lost). Sweeping must not undo it.'''
    _require_module()
    d = Path(tempfile.mkdtemp(prefix='t1540-c-'))
    try:
        out = d / 'session-review-t7x2q9pl' / 'answer.md'
        ask_q16.write_answer(str(out), {'text': BODY, 'thinking': THINKING, 'done_reason': 'stop'})
        th = Path(str(out) + '.thinking.md')
        assert th.is_file(), 'no .thinking.md: 30 minutes of model time written and then thrown away'
        assert th.read_text(encoding='utf-8') == THINKING, 'the reasoning on disk is not the reasoning produced'
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_4_the_empty_answer_record_survives_a_swept_dir():
    '''The failure record has the same exposure: a swept dir must not erase the evidence, and
    an empty answer must never fabricate a zero-byte answer.md (t311).'''
    _require_module()
    d = Path(tempfile.mkdtemp(prefix='t1540-d-'))
    try:
        out = d / 'session-review-k3m8n2vz' / 'answer.md'
        assert not out.parent.exists()
        got = ask_q16.write_answer(str(out), {'text': '', 'thinking': THINKING, 'done_reason': 'length'})
        side = Path(got)
        assert side.name.startswith('answer.md.failed-'), \
            'an empty answer is recorded in a .failed-<utc> sidecar, not in answer.md: %r' % (got,)
        assert side.is_file(), 'the empty run produced no record at all: %s' % (got,)
        assert json.loads(side.read_text(encoding='utf-8')).get('done_reason') == 'length'
        assert not out.exists(), 'an empty answer fabricated an answer.md: a file saying '\
                                'something that did not happen'
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_5_a_normal_out_path_is_written_verbatim_and_live_state_is_untouched():
    '''Neighbour guard: the fix adds a mkdir, it does not move, wrap or replace the write.'''
    _require_module()
    d = Path(tempfile.mkdtemp(prefix='t1540-e-'))
    findings = ROOT / 'workspace' / 'session-reviews' / 'findings'
    before = sorted(p.name for p in findings.glob('*.json'))
    try:
        job = d / 'session-review-existing'
        job.mkdir()
        out = job / 'answer.md'
        out.write_text(PRIOR, encoding='utf-8')
        got = ask_q16.write_answer(str(out), {'text': BODY, 'thinking': THINKING,
                                              'done_reason': 'stop'})
        assert got == str(out), 'the normal path must return the requested path: %r' % (got,)
        assert out.read_text(encoding='utf-8') == BODY, 'the normal path must write the new answer'
        assert sorted(str(p.relative_to(d)) for p in d.rglob('*')) == sorted([
            str(Path('session-review-existing') / 'answer.md'),
            str(Path('session-review-existing') / 'answer.md.meta.json'),
            str(Path('session-review-existing') / 'answer.md.thinking.md'),
            'session-review-existing']), \
            'write_answer wrote somewhere other than the path it was given'
        assert sorted(p.name for p in findings.glob('*.json')) == before, \
            'the oracle wrote into live state (workspace/session-reviews/findings/)'
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == '__main__':
    _fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    _bad = 0
    for _fn in _fns:
        try:
            _fn()
            print('ok   %s' % _fn.__name__)
        except Exception as _e:
            _bad += 1
            print('FAIL %s: %s: %s' % (_fn.__name__, type(_e).__name__, _e))
    print('%d/%d passed' % (len(_fns) - _bad, len(_fns)))
    sys.exit(1 if _bad else 0)
