#!/usr/bin/env python3
# t1377: a prose deferral discharged by a COMMIT is credited with that commit.
#
# WHY. workspace/rca/888ba4de5120.md: session dd5da968, assistant turn 226, verbatim "The patch is
# uncommitted in the working tree; I'll review it properly rather than take it on trust." The session
# DID the review -- it read the diff, caught the duplicated --num-predict argparse flag, fixed it and
# committed the patch with attribution. The promise ledger recorded that row as "undischarged", with
# evidence_channel "assistant-text" and artefact [] (workspace/session-reviews/promise-ledger.jsonl,
# turn:226), because scripts/session_review.py asks only the 6-turn assistant-text window
# (PROMISE_WINDOW = 6, line 49; record_promise_ledger, line 71) and has no channel that can read git
# history. A review evidenced by a commit is therefore invisible to it, and the finding re-dispatches.
#
# THE CONTRACT (this file is the freeze; a separate implementation dispatch implements it and may not
# edit this file).
#   session_review.on_disk_discharge(files, repo, since="") -> list of dicts
#     One dict per (commit, file) pair: {"commit": <full 40-hex sha>, "file": <repo-relative path>,
#     "subject": <first line of the commit message>}, newest commit first, taken from `git log` for
#     the repo at `repo` (str or Path) restricted to the paths in `files` and to commits at or after
#     the ISO-8601 `since` (an empty `since` is no lower bound). A path with no commits in the repo
#     contributes nothing and must not raise. A directory that is not a git repo contributes nothing
#     AND prints one line naming the repo path and the git failure -- a silently swallowed git error
#     is indistinguishable from a deferral that was never discharged.
#   session_review.record_promise_ledger(ts, session, out_path=None, discharges=None)
#     discharges maps a promise turn index to the artefact dicts on_disk_discharge returned for it.
#     A row whose turn has artefacts gains "git_artefacts" (the list, as given, same dicts, same
#     order), gains each artefact's "commit" value in "artefact", and its verdict/evidence_channel
#     follow the two-channel rule:
#       text matches AND artefacts  -> verdict "executed-in-text",      evidence_channel "assistant-text+git-log"
#       no text match AND artefacts -> verdict "discharged-by-commit",  evidence_channel "git-log"
#       no artefacts                -> today's values unchanged: "executed-in-text"/"undischarged"
#                                      and evidence_channel "assistant-text"
#     A turn with no promise row has no row to credit: a build that stamps artefacts onto every row
#     fails test_9. Every field the row carries today keeps its name and value (TODAY_FIELDS).
#
# REAL VALUES. The file path and the commit subject are the ones the RCA recorded for the real
# discharge (projects/local-inference/exam.py; "keep the Duo's --num-predict patch, with
# attribution"). The decoy file and subject are deliberately distinct, so an implementation that
# credits any commit in range passes a suite that checks nothing. No repeated placeholder is used.
#
# HERMETIC. Every git repo is created inside tempfile.TemporaryDirectory(); every git call is
# subprocess.run(..., timeout=60); no model call, no live server, no fixed /tmp path, no process left
# running; nothing outside the temp dirs is written. pytest is not installed: _run() runs every test
# and exits 1 if any fails, so `python3 scripts/tests/test_on_disk_discharge.py` is the whole suite.
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import session_review  # noqa: E402

GIT_TIMEOUT = 60
PROMISE = ("The patch is uncommitted in the working tree; I'll review it properly rather than "
           "take it on trust.")
FILLER = 'The re-test numbers are in: Q4 14/18 traps, gemma 0/18.'
PATCHED = 'projects/local-inference/exam.py'
PATCH_SUBJECT = "keep the Duo's --num-predict patch, with attribution"
OTHER = 'scripts/other.py'
OTHER_SUBJECT = 'refactor the queue picker'
SHA = 'ab12cd34ef567890'
ART = [{'commit': SHA, 'file': PATCHED, 'subject': PATCH_SUBJECT}]
TODAY_FIELDS = ('session', 'turn', 'quote', 'promise_phrase', 'matching_actions_within_n_turns',
                'artefact', 'verdict', 'evidence_channel', 'at')


def _git(repo, args, env=None):
    return subprocess.run(['git', '-C', str(repo)] + args, capture_output=True, text=True,
                          timeout=GIT_TIMEOUT, env=env)


def _commit(repo, rel, content, subject, date):
    """One commit on repo: write rel, add it, commit with fixed author/committer identity and date,
    return the full sha git assigned."""
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding='utf-8')
    assert _git(repo, ['add', '--', rel]).returncode == 0, 'git add failed'
    env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    cp = _git(repo, ['-c', 'user.email=fixture@example.invalid', '-c', 'user.name=fixture',
                     'commit', '-q', '-m', subject], env=env)
    assert cp.returncode == 0, 'git commit failed: ' + cp.stderr.strip()
    sha = _git(repo, ['rev-parse', 'HEAD'])
    assert sha.returncode == 0, 'git rev-parse failed: ' + sha.stderr.strip()
    return sha.stdout.strip()


def _repo(tmp):
    """Two commits, dated a month apart: the exam.py patch (2026-09-04) and an unrelated
    scripts/other.py commit (2026-10-17). Returns the shas the real git assigned."""
    assert _git(tmp, ['init', '-q']).returncode == 0, 'git init failed'
    patched = _commit(tmp, PATCHED, 'def ask(): pass\n', PATCH_SUBJECT, '2026-09-04T12:00:00+00:00')
    other = _commit(tmp, OTHER, 'x = 1\n', OTHER_SUBJECT, '2026-10-17T12:00:00+00:00')
    return {'patched': patched, 'other': other}


def _ts_with_promise():
    return [('assistant', FILLER), ('assistant', PROMISE), ('assistant', FILLER)]


def _rows(out):
    return [json.loads(l) for l in Path(out).read_text(encoding='utf-8').splitlines() if l.strip()]


def test_1_the_named_files_commit_is_returned_exact():
    # 1. The real case, asserted by value: the full sha, the repo-relative path, the real subject.
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        got = session_review.on_disk_discharge([PATCHED], d)
        assert got == [{'commit': r['patched'], 'file': PATCHED, 'subject': PATCH_SUBJECT}], repr(got)


def test_2_a_commit_touching_an_unnamed_file_credits_nothing():
    # 2. Path-scoping. Asking for scripts/other.py must return that commit's own sha and subject, and
    #    asking for the patch file must not sweep the decoy in: an unpathspec'd `git log` passes 1 and
    #    fails here.
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        assert session_review.on_disk_discharge([OTHER], d) == [
            {'commit': r['other'], 'file': OTHER, 'subject': OTHER_SUBJECT}], repr(
            session_review.on_disk_discharge([OTHER], d))
        assert session_review.on_disk_discharge([PATCHED], d) == [
            {'commit': r['patched'], 'file': PATCHED, 'subject': PATCH_SUBJECT}], (
            'the decoy commit leaked into the patch deferral\'s artefacts')


def test_3_both_named_files_report_newest_first():
    # 3. Two files, two distinct shas, newest commit first (git log order). Order is a record property:
    #    a reader scanning the row reads top-down.
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        assert session_review.on_disk_discharge([PATCHED, OTHER], d) == [
            {'commit': r['other'], 'file': OTHER, 'subject': OTHER_SUBJECT},
            {'commit': r['patched'], 'file': PATCHED, 'subject': PATCH_SUBJECT}], repr(
            session_review.on_disk_discharge([PATCHED, OTHER], d))


def test_4_since_excludes_a_commit_that_predates_the_promise():
    # 4. A commit that predates the promise is not that promise's discharge. The 2026-09-04 patch
    #    commit must drop out under a 2026-10-01 lower bound, and both must be present with no bound:
    #    pinning both directions is what stops a build that ignores `since`.
    with tempfile.TemporaryDirectory() as d:
        r = _repo(d)
        got = session_review.on_disk_discharge([PATCHED, OTHER], d, since='2026-10-01T00:00:00+00:00')
        assert got == [{'commit': r['other'], 'file': OTHER, 'subject': OTHER_SUBJECT}], (
            'the 2026-09-04 commit predates the promise and must drop out: ' + repr(got))
        assert len(session_review.on_disk_discharge([PATCHED, OTHER], d)) == 2, (
            'with no since bound both commits are on disk')


def test_5_paths_are_normalised_and_a_git_failure_is_announced():
    # 5. Absolute paths come back repo-relative; a path with no commits yields [] and must not raise;
    #    a non-repo directory yields [] AND prints the repo path with the cause -- a swallowed git
    #    error reads exactly like a deferral nobody discharged.
    with tempfile.TemporaryDirectory() as d:
        _repo(d)
        got = session_review.on_disk_discharge([str(Path(d) / PATCHED)], d)
        assert [e['file'] for e in got] == [PATCHED], (
            'an absolute path must come back repo-relative: ' + repr(got))
        assert session_review.on_disk_discharge(['does/not/exist.py'], d) == [], (
            'a path with no commits contributes nothing and must not raise')
    with tempfile.TemporaryDirectory() as plain:
        err = io.StringIO()
        with redirect_stderr(err):
            out = session_review.on_disk_discharge(['x.py'], plain)
        assert out == [], 'a non-repo directory yields no artefacts, got ' + repr(out)
        text = err.getvalue()
        assert str(plain) in text and 'git' in text.lower(), (
            'a git failure must be printed with the repo path and the cause, not swallowed: ' + repr(text))


def test_6_the_commit_reaches_the_row_the_promise_made():
    # 6. THE VALUE IS USED, NOT JUST RETURNED: record_promise_ledger has to write it onto the row, the
    #    only record a later gate can consult. Field names are asserted so the failure says what is
    #    missing. The text window finds nothing for this quote, so the git channel is the verdict.
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'promise-ledger.jsonl'
        rows = session_review.record_promise_ledger(_ts_with_promise(), 't1377-fixture',
                                                   out_path=out, discharges={1: ART})
        assert out.is_file(), 'record_promise_ledger wrote nothing to out_path ' + str(out)
        written = _rows(out)
    assert len(rows) == 1 and len(written) == 1, 'expected exactly 1 row, got ' + str(len(written))
    row = written[0]
    assert row.get('git_artefacts') == ART, ('git_artefacts is not the list that was passed, as given: '
                                             + repr(row.get('git_artefacts')))
    assert SHA in row['artefact'], 'the commit sha is not in the row artefact list: ' + repr(row['artefact'])
    assert row.get('verdict') == 'discharged-by-commit', 'verdict: ' + repr(row.get('verdict'))
    assert row.get('evidence_channel') == 'git-log', 'evidence_channel: ' + repr(row.get('evidence_channel'))
    for k in TODAY_FIELDS:
        assert k in row, ('record_promise_ledger no longer writes ' + k + ': ' + repr(sorted(row)))
    assert row.get('turn') == 1, 'the row is not the promise turn: ' + repr(row.get('turn'))


def test_7_a_text_discharge_and_a_commit_are_recorded_together():
    # 7. Both channels fire: the text verdict is NOT downgraded, and the channel names both. The
    #    follow-up sentence shares the promise's exact surface words (patch, working, tree,
    #    uncommitted, review, trust, properly) and contains no PROMISE_PHRASES phrase, so it makes a
    #    match and no row of its own.
    ts = [('assistant', FILLER), ('assistant', PROMISE),
          ('assistant', 'I reviewed the patch in the working tree; it is no longer uncommitted, '
                        'and the review earned trust, properly.')]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'promise-ledger.jsonl'
        session_review.record_promise_ledger(ts, 't1377-fixture', out_path=out, discharges={1: ART})
        written = _rows(out)
    assert len(written) == 1, 'expected exactly 1 row, got ' + str(len(written))
    row = written[0]
    assert row['matching_actions_within_n_turns'] == 1, repr(row.get('matching_actions_within_n_turns'))
    assert row.get('verdict') == 'executed-in-text', repr(row.get('verdict'))
    assert row.get('evidence_channel') == 'assistant-text+git-log', repr(row.get('evidence_channel'))
    assert row.get('git_artefacts') == ART, repr(row.get('git_artefacts'))


def test_8_the_today_row_shape_survives_when_no_commit_is_offered():
    # 8. No discharges passed: today's row, field for field, plus git_artefacts == [] (an empty list,
    #    not a missing key) so a reader can tell "looked, found nothing" from "never looked".
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'promise-ledger.jsonl'
        session_review.record_promise_ledger(_ts_with_promise(), 't1377-fixture', out_path=out)
        written = _rows(out)
    assert len(written) == 1, 'expected exactly 1 row, got ' + str(len(written))
    row = written[0]
    for k in TODAY_FIELDS:
        assert k in row, 'row lost ' + k + ': ' + repr(sorted(row))
    assert row['verdict'] == 'undischarged', repr(row['verdict'])
    assert row['evidence_channel'] == 'assistant-text', repr(row['evidence_channel'])
    assert row['artefact'] == [], repr(row['artefact'])
    assert row.get('git_artefacts') == [], ('an undischarged row must carry an empty git_artefacts, '
                                            'not a missing key: ' + repr(sorted(row)))


def test_9_an_artefact_for_a_turn_that_made_no_promise_credits_nothing():
    # 9. The ledger is a record of promises, not a place to park shas. The session's only promise is
    #    at turn 1; artefacts offered for turn 2 must not be stamped onto that row (a build that
    #    appends discharges to every row passes 6-8 and fails here).
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / 'promise-ledger.jsonl'
        session_review.record_promise_ledger(_ts_with_promise(), 't1377-fixture', out_path=out,
                                             discharges={2: ART})
        written = _rows(out)
    assert len(written) == 1, 'expected exactly 1 row, got ' + str(len(written))
    blob = json.dumps(written)
    assert SHA not in blob, ('a commit was credited to a promise that did not name that work: ' + blob)
    assert written[0].get('git_artefacts') == [], repr(written[0].get('git_artefacts'))


def _run():
    tests = [test_1_the_named_files_commit_is_returned_exact,
             test_2_a_commit_touching_an_unnamed_file_credits_nothing,
             test_3_both_named_files_report_newest_first,
             test_4_since_excludes_a_commit_that_predates_the_promise,
             test_5_paths_are_normalised_and_a_git_failure_is_announced,
             test_6_the_commit_reaches_the_row_the_promise_made,
             test_7_a_text_discharge_and_a_commit_are_recorded_together,
             test_8_the_today_row_shape_survives_when_no_commit_is_offered,
             test_9_an_artefact_for_a_turn_that_made_no_promise_credits_nothing]
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
