'''t1532: a session-review dispatch writes its prompt and answer under the disk temp root, and
the tmp sweeper deletes them while the call is in flight.

Two RCA attempts on 2026-10-20 recorded 'no answer (timed out or empty)' while the model had in
fact answered. The child died inside ask_q16.py with FileNotFoundError on a path ending
session-review-h8vf78y0/answer.md after 13723 chars with done_reason=stop, and on a path ending
session-review-4o1ugnvz/prompt.txt. session_review.ask() makes its scratch dir with
tempfile.mkdtemp(prefix='session-review-'), which lands under the disk temp root that
scripts/sweep.py cleans: TMP_PREFIXES names 'session-review-' explicitly (sweep.py line 411-412),
TMP_MAX_AGE_S is 3600 (line 413), the cron sweep walks that root every 15 minutes, and
sweep_after_interruption does it at RESUME_MIN_AGE_S = 300 (line 522). An RCA part runs about 19
minutes and ask() re-invokes the child on a held lease (rc 3/4) for up to its 6-hour wait, so the
directory is deleted while the call is in flight. tmp_holders() (line 417) protects only a
directory a process is INSIDE of -- its cwd, an open fd -- and a dispatch in flight holds a
socket, not an fd into its scratch dir, so it is eligible.

Red on main for one reason: session_review has no dispatch_scratch_dir. The strongest test asks
the real sweeper -- imported from scripts/sweep.py, not a copy of its rules -- whether the name
the fix produced is eligible to delete, so a fix that keeps the session-review- prefix and only
moves the location fails: tmp_eligible matches on the NAME. No model call, no server, no pytest,
no subprocess. Every directory created here is removed by _clean().
'''
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import session_review
import sweep

SEEN = []
PURPOSES = ['d4913649a026.json--rca--swift--1792535825',
            'd4913649a026.json--rca--qwen3.8_27b-mtp-q4_K_M-56k--1792534517',
            'session review: claude-fa510770-2369-48d8-9f82-e4ee869f8554 part 1/6']


def _scratch(purpose):
    d = session_review.dispatch_scratch_dir(purpose)
    SEEN.append(d)
    return d


def _clean():
    # Remove every directory this file created, then prune the empty parents it left behind,
    # stopping at (not above) the repository root. Nothing this run created is left for the
    # worktree gate to find, wherever a build chooses to put the durable root.
    for d in SEEN:
        shutil.rmtree(d, ignore_errors=True)
        p = d.parent
        while ROOT in p.parents:
            try:
                p.rmdir()
            except OSError:
                break
            p = p.parent
    del SEEN[:]


def test_the_scratch_dir_is_created_and_is_a_directory():
    d = _scratch(PURPOSES[0])
    assert d.is_dir(), 'dispatch_scratch_dir must return a directory that exists: ' + str(d)


def test_it_is_not_under_a_swept_temp_root():
    d = _scratch(PURPOSES[0]).resolve()
    swept = [Path(tempfile.gettempdir()).resolve(), (Path.home() / '.cache' / 'hivemind-tmp').resolve()]
    for s in swept:
        assert d != s and s not in d.parents, (
            'the tmp sweeper walks ' + str(s) + ', so a dispatch scratch dir under it is deleted '
            'while the call is in flight (this is t1532). ' + str(d) + ' sits under it')


def test_the_tmp_sweeper_cannot_eligible_a_live_dispatch():
    d = _scratch(PURPOSES[0])
    assert not sweep.tmp_eligible(d.name, 10 ** 9, set(), str(d)), (
        'the real sweeper says a scratch dir named ' + d.name + ' can be deleted at any age: a '
        '19-minute RCA part loses its prompt.txt and answer.md mid-call, which is how the model '
        'came to be blamed for a filesystem race')
    bad = [p for p in sweep.TMP_PREFIXES if d.name.startswith(p)]
    assert not bad, (
        'the scratch name ' + d.name + ' starts with a swept prefix ' + str(bad) + ': the '
        'sweeper matches on the NAME, so a durable location wearing a swept name is swept too')


def test_the_child_answer_survives_and_a_retry_reuses_the_same_dir():
    d = _scratch(PURPOSES[0])
    (d / 'answer.md').write_text('RCA written at 13723 chars, done_reason=stop', encoding='utf-8')
    again = _scratch(PURPOSES[0])
    assert again == d, (
        'ask() re-invokes the child (rc 3/4, lease held) with the same prompt path it wrote at '
        'the start, so the scratch dir must be stable per purpose: first ' + str(d)
        + ' second ' + str(again))
    got = (again / 'answer.md').read_text(encoding='utf-8') if (again / 'answer.md').is_file() else ''
    assert got.startswith('RCA written'), 'the answer the child wrote did not survive the retry'


def test_distinct_purposes_get_distinct_unnested_dirs():
    a = _scratch(PURPOSES[0])
    b = _scratch(PURPOSES[1])
    assert a != b, 'two dispatches must not share one scratch dir'
    assert a not in b.parents and b not in a.parents, (
        'one dispatch must not be able to overwrite the other file: ' + str(a) + ' vs ' + str(b))
    (a / 'answer.md').write_text('one', encoding='utf-8')
    assert (a / 'answer.md').read_text(encoding='utf-8') == 'one', 'a scratch dir that cannot hold a file'


def test_a_purpose_cannot_escape_the_scratch_root():
    d = _scratch('../../etc/evil purpose')
    assert d.name and '/' not in d.name, 'the sanitised name must be one path component: ' + repr(d.name)
    assert ROOT in d.parents, 'the durable scratch root must be inside the repository: ' + str(d)
    assert len(d.name) <= 160, 'the name must fit one path component: ' + str(len(d.name))


def test_ask_uses_the_durable_scratch_dir():
    import inspect
    body = inspect.getsource(session_review.ask)
    assert 'dispatch_scratch_dir' in body, 'ask() must build its scratch dir with dispatch_scratch_dir(purpose)'
    assert 'mkdtemp' not in body, 'ask() still calls tempfile.mkdtemp, the swept location (t1532)'


def _run():
    tests = sorted((n, f) for n, f in globals().items() if n.startswith('test_') and callable(f))
    failed = []
    try:
        for n, f in tests:
            try:
                f()
                print('ok   ' + n)
            except Exception as e:
                failed.append(n)
                print('FAIL ' + n + ': ' + type(e).__name__ + ': ' + str(e)[:180])
    finally:
        _clean()
    if failed:
        print('FAILED ' + str(len(failed)) + ' of ' + str(len(tests)))
        return 1
    print('all ' + str(len(tests)) + ' tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(_run())
