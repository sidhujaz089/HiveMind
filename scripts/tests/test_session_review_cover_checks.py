#!/usr/bin/env python3
'''t1437 -- the cover search records its own terms, candidates, git query and answer.

Red on main for ONE reason: scripts/session_review.py defines no COVER_CHECKS and no
record_cover_check, so verify_cover() consults every task candidate and every `git log -S` term and
keeps none of it. RCA ee154c612ac9 section 4, DE-1: 'Nothing records why 07f933e5 was never offered
as a cover.'

It also runs the -S/hash probe that RCA left unexecuted, in a git repo this file builds inside a temp
dir: `git log -S<the commit's own 40-hex hash>` finds nothing, while `git log -S<text its own diff
added>` finds the commit. That is the mechanism that made 07f933e5 un-offerable, demonstrated, not
asserted.

No model, no live server, no process left running, no fixed /tmp path, and no git against the live
tree: every write goes inside a tempfile.TemporaryDirectory.

Run: python3 scripts/tests/test_session_review_cover_checks.py   (pytest is not installed)
'''
import ast
import inspect
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))

import session_review as sr  # noqa: E402

FIELDS = {'key', 'terms', 'tasks_seen', 'commits_seen', 'git_cmd', 'answer', 'examined_count', 'at'}
Z = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
MARKER = 'preferences-rca-07f933e5-marker'
TERMS = ['docs/PREFERENCES.md', '07f933e5']
TASKS_SEEN = ['t1437', 't950', 't1095']
GIT_CMD = [['git', '-C', '/repo', 'log', '--since=2026-10-02 00:00:00', '--format=%h %ad %s',
            '--date=short', '-n', '3', '-Sdocs/PREFERENCES.md', '--', '.']]


def _writer():
    f = getattr(sr, 'record_cover_check', None)
    assert callable(f), (
        'scripts/session_review.py defines no record_cover_check, so the cover search (verify_cover) '
        'consults the task candidates and the `git log -S` pickaxe and records nothing: a later reader '
        'asking what the search looked at, and what it answered, has no artefact to read (t1437, '
        'RCA ee154c612ac9 DE-1).')
    return f


def _rows(path):
    return [json.loads(l) for l in Path(path).read_text(encoding='utf-8').splitlines() if l.strip()]


def _git(args, cwd):
    cp = subprocess.run(['git', '-c', 'user.email=t@example.invalid', '-c', 'user.name=t1437',
                         '-c', 'commit.gpgsign=false', '-c', 'init.defaultBranch=master'] + list(args),
                        capture_output=True, text=True, timeout=120, cwd=str(cwd))
    assert cp.returncode == 0, 'git %s exited %d: %s' % (' '.join(args), cp.returncode, cp.stderr)
    return cp.stdout


def test_a_commit_hash_is_unfindable_by_the_pickaxe_that_searches_for_it():
    '''The RCA's DE-1, executed for the first time. A commit's own hash is not content inside its own
    diff, so `git log -S<hash>` cannot return the commit it names -- which is why the quote's strongest
    identifier (07f933e5) was never offered as a cover. Run in a repo this test builds, in a temp dir,
    so it touches no live state.'''
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d) / 'repo'
        repo.mkdir()
        _git(['init', '-q'], repo)
        (repo / 'PREFERENCES.md').write_text('the shared preferences file: ' + MARKER + '\n',
                                            encoding='utf-8')
        _git(['add', '-A'], repo)
        _git(['commit', '-q', '-m', 'docs(workspace): record the preference'], repo)
        head = _git(['rev-parse', 'HEAD'], repo).strip()
        assert re.fullmatch(r'[0-9a-f]{40}', head), 'a commit hash to probe with, got %r' % (head,)
        by_hash = _git(['log', '--all', '-n', '3', '--oneline', '-S' + head], repo)
        assert by_hash.strip() == '', (
            'git log -S<the commit\'s own 40-hex hash> returned %r in a repo where that commit '
            'exists. The pickaxe CAN find a commit by its own hash, so the mechanism the RCA named is '
            'not why 07f933e5 was missed and this task\'s premise must be re-scoped.' % (by_hash,))
        by_content = _git(['log', '--all', '-n', '3', '--oneline', '-S' + MARKER], repo)
        assert head[:7] in by_content, (
            'the pickaxe did not find the commit by the text its own diff added (%r): this probe is '
            'not built the way commit_candidates searches, so it proves nothing' % (by_content,))


def test_record_cover_check_appends_one_row_per_call_with_the_named_fields():
    '''One row per finding, appended, carrying the field set the task names -- stored as the search
    produced them, in the search's own order.'''
    write = _writer()
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'cover-checks.jsonl'
        r1 = write(key='ee154c612ac9', terms=list(TERMS), tasks_seen=list(TASKS_SEEN),
                   commits_seen=[], git_cmd=list(GIT_CMD), answer='none',
                   examined_count=len(TASKS_SEEN), out_path=out)
        rows = _rows(out)
    assert len(rows) == 1, 'one call appends one row, got %d: %r' % (len(rows), rows)
    assert r1 == rows[0], (
        'the writer must return the row it appended, so a caller can log what it recorded -- '
        'returned %r, file holds %r' % (r1, rows[0]))
    r = rows[0]
    assert set(r) == FIELDS, 'row fields are the contract: expected %s, got %s' % (sorted(FIELDS), sorted(r))
    assert r['key'] == 'ee154c612ac9', (
        'key is the caller\'s finding key verbatim -- the only field that joins this row to the '
        'finding whose cover was checked, got %r' % (r['key'],))
    assert r['terms'] == TERMS, (
        'terms is the search\'s own term list, in order: the RCA\'s question is what the term filter '
        'kept out, and a reordered or filtered list cannot answer it, got %r' % (r['terms'],))
    assert all(isinstance(t, str) for t in r['terms']), 'terms is a list of strings, got %r' % (r['terms'],)
    assert r['tasks_seen'] == TASKS_SEEN, 'tasks_seen is the task ids the search found, got %r' % (r['tasks_seen'],)
    assert r['commits_seen'] == [], 'this search found no commit, and that is the finding, got %r' % (r['commits_seen'],)
    assert r['git_cmd'] == GIT_CMD, (
        'git_cmd is the argv the pickaxe ran, verbatim (the RCA\'s dead end is a command, not a prose '
        'description), got %r' % (r['git_cmd'],))
    assert r['answer'] == 'none', 'the answer is the judge\'s reply verbatim, got %r' % (r['answer'],)
    assert isinstance(r['examined_count'], int) and not isinstance(r['examined_count'], bool), (
        'examined_count is a count, got %r' % (r['examined_count'],))
    assert r['examined_count'] == len(TASKS_SEEN), (
        'examined_count counts the candidates the search examined, got %r' % (r['examined_count'],))
    assert Z.match(str(r['at'])), 'at=%r is not a Z timestamp' % (r['at'],)


def test_a_cover_search_that_found_nothing_is_still_recorded():
    '''The RCA's case is a search that found NOTHING: the pickaxe returned no commit and the finding
    was minted with covered_by empty. A log that records only the searches that found something is
    silent on exactly the rows that need explaining (check-satisfied-by-dead-path).'''
    write = _writer()
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'cover-checks.jsonl'
        write(key='', terms=[], tasks_seen=[], commits_seen=[], git_cmd=[], answer='',
              examined_count=0, out_path=out)
        rows = _rows(out)
    assert len(rows) == 1, 'an empty search must write a row, got %d rows' % len(rows)
    r = rows[0]
    assert r['terms'] == [] and r['tasks_seen'] == [] and r['commits_seen'] == [], (
        'the empty search is stored empty, got %r' % (r,))
    assert r['examined_count'] == 0, 'a search that examined nothing says 0, got %r' % (r['examined_count'],)


def test_the_log_appends_across_findings_and_sessions():
    '''Append-only, one row per finding, in call order: the file is evidence a later session reads in
    bulk, and a writer that overwrites loses every earlier dead end.'''
    write = _writer()
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'cover-checks.jsonl'
        write(key='aaaaaaaaaaaa', terms=list(TERMS), tasks_seen=[], commits_seen=[],
              git_cmd=list(GIT_CMD), answer='none', examined_count=1, out_path=out)
        write(key='bbbbbbbbbbbb', terms=[], tasks_seen=['t1437'], commits_seen=[],
              git_cmd=[], answer='', examined_count=1, out_path=out)
        rows = _rows(out)
    assert [r['key'] for r in rows] == ['aaaaaaaaaaaa', 'bbbbbbbbbbbb'], (
        'append-only, one row per finding, in call order: %r' % ([r['key'] for r in rows],))
    assert [r['answer'] for r in rows] == ['none', ''], (
        'each row carries its own finding\'s answer: %r' % ([r['answer'] for r in rows],))
    for r in rows:
        assert set(r) == FIELDS, 'every row carries the same field set, got %s' % sorted(r)


def test_the_judge_s_long_answer_is_stored_whole():
    '''A cover answer is a record, not a display string: cutting it at N characters is the defect this
    repository has a whole skill about (never-truncate-a-record). Stored byte-ident at any length.'''
    write = _writer()
    long_answer = 'fixed:' + 'ab' * 4000
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'cover-checks.jsonl'
        write(key='cccccccccccc', terms=[], tasks_seen=[], commits_seen=[], git_cmd=[],
              answer=long_answer, examined_count=0, out_path=out)
        rows = _rows(out)
    assert len(rows) == 1, 'got %d rows' % len(rows)
    assert rows[0]['answer'] == long_answer, (
        'the answer is stored cut to %d of %d characters: a cover log whose answer is truncated is a '
        'record with the verdict thrown away' % (len(rows[0]['answer']), len(long_answer)))


def test_the_cover_log_is_its_own_file_and_never_the_review_log():
    '''workspace/session-reviews/ledger.jsonl says a review step RAN, and promise-ledger.jsonl says a
    promise was kept. This says what one cover search consulted and answered. Readers of those files
    keep parsing what they parse today, so it is a new file.'''
    _writer()
    assert hasattr(sr, 'COVER_CHECKS'), 'no COVER_CHECKS constant: the log\'s path is undeclared'
    target = str(sr.COVER_CHECKS)
    assert target.endswith('workspace/session-reviews/cover-checks.jsonl'), 'COVER_CHECKS=%r' % (target,)
    assert target != str(sr.LEDGER), 'COVER_CHECKS must not be the review log: %r' % (target,)
    assert target != str(sr.PROMISE_LEDGER), (
        'the promise ledger (t950) records promises, this records cover searches: %r' % (target,))


def test_the_default_path_is_the_module_constant():
    '''The endpoint calls the writer with no path, so only the module default decides where the row
    lands -- an unwired default leaves the production file empty while every other test is green.
    Reads the signature; writes nothing.'''
    write = _writer()
    p = inspect.signature(write).parameters.get('out_path')
    assert p is not None and p.default is not inspect.Parameter.empty, (
        'record_cover_check has no out_path default, so the cover endpoint writes nowhere')
    assert str(p.default) == str(sr.COVER_CHECKS), (
        'out_path defaults to %r, not COVER_CHECKS=%r' % (str(p.default), str(sr.COVER_CHECKS)))


def test_a_cover_log_that_cannot_be_written_says_why():
    '''A writer that lets the OSError escape dies inside a review (this file's standing rule: 'I don't
    want it to block you'); one that swallows it reads as 'this search had nothing to record'. The
    handler must record what it caught -- the shape record_promise_ledger's own write handler uses.'''
    src = inspect.getsource(_writer())
    assert 'OSError' in src, (
        'record_cover_check catches no OSError: on a read-only or full filesystem the cover search '
        'either kills the review or vanishes, and a vanished row is the exact failure class this task '
        'exists to close (swallowed-exception-introduced)')
    assert 'stderr' in src, (
        'the write failure is announced nowhere a reader sees: silence reads as "nothing to log"')


def _verify_cover_fn():
    tree = ast.parse(inspect.getsource(sr))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'verify_cover':
            return node
    raise AssertionError('scripts/session_review.py defines no top-level verify_cover')


def test_the_cover_endpoint_records_every_search_it_runs():
    '''A writer no endpoint calls leaves cover-checks.jsonl empty while every writer test above is
    green. verify_cover returns from five places and the nothing-found return is the RCA's row, so a
    recorder on one path records half the log -- and the values recorded must be the search's own,
    not [] and '' passed as stubs. Checked per argument, because one joined blob lets a constant in
    one field hide behind a real value in another.'''
    fn = _verify_cover_fn()
    src = inspect.getsource(sr)
    calls = [c for c in ast.walk(fn)
             if isinstance(c, ast.Call) and getattr(c.func, 'id', '') == 'record_cover_check']
    assert len(calls) >= 2, (
        'verify_cover calls record_cover_check %d time(s); it returns from five places, and the '
        'nothing-found return is the dead end the RCA asked about, so a single call records a search '
        'that found something and stays silent on the one that found nothing' % len(calls))
    names = ('key', 'terms', 'tasks_seen', 'commits_seen', 'git_cmd', 'answer', 'examined_count')

    def arg(call, name):
        for k in call.keywords:
            if k.arg == name:
                return ast.get_source_segment(src, k.value) or ''
        return ''

    for c in calls:
        absent = [n for n in names if not arg(c, n)]
        assert not absent, (
            'a record_cover_check call in verify_cover passes no %s: a row written without the '
            'search\'s own fields is a record that cannot answer why a cover was missed'
            % (', '.join(absent),))
    for field, token in (('terms', 'commit_terms'), ('git_cmd', 'commit_query'), ('answer', 'ans'),
                         ('tasks_seen', 'tasks'), ('commits_seen', 'commits'),
                         ('examined_count', 'len('), ('key', 'key')):
        assert any(token in arg(c, field) for c in calls), (
            'no record_cover_check call takes %s from %s, so a build can store a constant there and '
            'keep this suite green (the terms list, the pickaxe argv, the judge\'s reply, the '
            'candidate lists and the examined count must all be the search\'s own values)'
            % (field, token))


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    assert len(tests) >= 8, 'the suite defines %d tests; expected at least 8' % (len(tests),)
    failed = 0
    for t in tests:
        try:
            t()
            print('ok   %s' % (t.__name__,))
        except AssertionError as e:
            failed += 1
            print('FAIL %s: %s' % (t.__name__, e))
        except Exception as e:
            failed += 1
            print('FAIL %s: %s: %s' % (t.__name__, type(e).__name__, e))
    print('%d test(s), %d failed' % (len(tests), failed))
    sys.exit(1 if failed else 0)
