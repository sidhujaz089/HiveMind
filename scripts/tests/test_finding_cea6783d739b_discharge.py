#!/usr/bin/env python3
'''Oracle for t1116 (2026-10-17 half): re-disposition finding cea6783d739b.

Task:  workspace/tasks/t1116--close-finding-be9a9cc851eb-as-resolved-via-commi.md (QUEUE.md:224),
       notes 2026-10-17, quoting workspace/rca/cea6783d739b.md.
CHANGE being pinned: "Rewrite workspace/session-reviews/findings/cea6783d739b.json:
       disposition=resolved-in-session, covered_by=fixed:271023a8, evidence = transcript seq
       3810/4138/4185 and git show --stat 271023a8".

WHY (cause, from the RCA and the task text): the promised work -- a notice when the test sweep
finished, the handover it asked for, and letting it commit -- was done inside the session, so the
record that keeps saying the promise is outstanding is a phantom: a later session reads it as open
deferral work and files an RCA for it again, forever.

READ-OFF-DISK FACTS THIS SUITE IS BUILT ON (each read this session, so no assertion rests on a
guess; the values below are the record's own, verbatim):
  * workspace/session-reviews/findings/done/cea6783d739b.json exists; key='cea6783d739b',
    kind='deferral', quote as QUOTE below, where='assistant, turn 236 of 266',
    session='d4c1cece-b307-4882-8c60-082b35869d28', outcome_task='t1052',
    outcome_status='queued', rca_report='workspace/rca/cea6783d739b.md',
    completed_at='2026-10-18T04:32:48Z', unusable=[{at: 2026-10-18T03:44:42Z, why: no answer
    (timed out or empty)}], disposition='covered', covered_by='t1052'.
  * There is NO second copy at workspace/session-reviews/findings/cea6783d739b.json, so
    'move it out of the open pile' needs no file move; the open-path test below is a guard
    against a build that creates one (a finding readable as OPEN from findings/ and CLOSED
    from findings/done/ is the split t1459's back-fill suite exists to remove).
  * DISPOSITIONS (scripts/session_review.py:935) is ('unresolved', 'resolved-in-session',
    'deferred', 'for-jas', 'refuted'): 'covered' is not legal, and 'resolved' -- the word in
    this task's own title -- is not legal either. The enum is ratcheted by
    scripts/tests/test_session_review_verification_claims.py:221 (len(DISPOSITIONS) == 5) and
    scripts/tests/test_preflight_preconditions.py:10 warns that dropping a value turns that
    suite red, so a build cannot legalise 'covered'. The value the RCA asks for is legal.
  * scripts/script_audit.py:229 treats only 'resolved-in-session' and 'refuted' as settled --
    that is the reader whose answer this change is meant to change.

RED ON MAIN FOR ONE REASON: the record carries none of the re-disposition, so each assertion
fires on a missing key or on the value quoted above. No writes, no subprocess, no git (see
scripts/tests/test_finding_2c6800f20445_binding.py:25-29: a builder sandbox is not a git repo),
no model call, no live server, no fixed /tmp path, no process left running. pytest is NOT
installed: run as python3 scripts/tests/test_finding_cea6783d739b_discharge.py.
'''
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'scripts'
RECORD_DONE = ROOT / 'workspace' / 'session-reviews' / 'findings' / 'done' / (KEY_PLACEHOLDER := 'cea6783d739b') + '.json'
RECORD_OPEN = ROOT / 'workspace' / 'session-reviews' / 'findings' / (KEY_PLACEHOLDER) + '.json'

KEY = 'cea6783d739b'
RCA = 'workspace/rca/cea6783d739b.md'
QUOTE = "I'll get a notice when it finishes, then fill in the handover it asks for and let it commit."
SESSION = 'd4c1cece-b307-4882-8c60-082b35869d28'
WHERE = 'assistant, turn 236 of 266'
COMPLETED_AT = '2026-10-18T04:32:48Z'
OUTCOME_TASK = 't1052'
LEGAL = 'resolved-in-session'
DEAD = 'covered'
COVERED_BY = 'fixed:271023a8'
SHA = '271023a8'
HANDOVER = 'workspace/HANDOVER-2026-10-08.md'
SEQS = ('3810', '4138', '4185')
PROVENANCE = ('key', 'kind', 'quote', 'context', 'where', 'found', 'session', 'harness',
              'request', 'rca_report', 'completed_at', 'outcome_task', 'outcome_status',
              'candidates_consulted', 'unusable')


def sr_mod():
    '''The writer module, imported inside a function and reported as a FAILED ASSERTION, so an
    import problem is this suite being red for its own bug and never a traceback.'''
    sys.path.insert(0, str(SCRIPTS))
    try:
        import session_review
    except Exception as exc:
        raise AssertionError(
            'scripts/session_review.py cannot be imported (%r). This suite reads DISPOSITIONS '
            'from that module at test time -- never a frozen copy of the list -- so the record '
            'is judged by the vocabulary the writer enforces.' % (exc,))
    return session_review


def record():
    '''The finding as it sits on disk. No synthetic fallback: the re-disposition's target IS
    this file, and a fixture copy is exactly how a back-fill suite goes vacuous.'''
    if not RECORD_DONE.is_file():
        raise AssertionError(
            'the finding record is not on disk at %s -- this suite pins the record on disk, so '
            'it refuses to fall back to a copy of it. Check the path with: ls -la '
            'workspace/session-reviews/findings/done/ | grep cea678' % (RECORD_DONE,))
    parsed = json.loads(RECORD_DONE.read_text(encoding='utf-8'))
    assert isinstance(parsed, dict), (
        'the record must stay a JSON object that json.load parses; it parsed as %s'
        % (type(parsed).__name__,))
    return parsed


def test_record_is_the_finding_this_task_re_dispositions():
    '''ANTI-VACUITY, green today: the file this suite binds is this record, and the fields its
    readers key on keep their exact values. A build that rewrites the record goes red here.'''
    rec = record()
    assert rec.get('key') == KEY, 'not the record t1116 re-dispositions: %r' % (rec.get('key'),)
    assert rec.get('kind') == 'deferral', 'kind must stay deferral: %r' % (rec.get('kind'),)
    assert rec.get('quote') == QUOTE, (
        'the promised sentence must survive the edit verbatim, found %r' % (rec.get('quote'),))
    assert rec.get('where') == WHERE, 'where must stay %r, found %r' % (WHERE, rec.get('where'))
    assert rec.get('rca_report') == RCA, (
        'rca_report must stay %r, found %r -- the RCA is why this record is being touched'
        % (RCA, rec.get('rca_report')))


def test_no_split_copy_of_the_record_exists():
    '''Green today. findings/ is scanned as the open pile and findings/done/ as the closed one;
    a record present in both reads as open to half the readers.'''
    assert not RECORD_OPEN.exists(), (
        'a second copy of the record exists at %s, so the same finding is OPEN to a reader of '
        'findings/ and CLOSED to a reader of findings/done/' % (RECORD_OPEN,))


def test_the_record_is_dispositioned_resolved_in_session():
    '''RED TODAY (found: 'covered'). The promise was discharged inside the reviewed session, so
    the record must stop claiming outstanding work.'''
    rec = record()
    assert rec.get('disposition') == LEGAL, (
        'disposition is %r; the RCA at %s prescribes %r, and scripts/script_audit.py:229 counts '
        'only resolved-in-session/refuted as settled, so any other value leaves this finding '
        'open work for the next session to re-RCA.' % (rec.get('disposition'), RCA, LEGAL))
    assert rec.get('disposition') != DEAD, (
        'disposition must not stay %r: it is not in session_review.DISPOSITIONS and it points '
        'the reader at a tracker instead of at the commit that did the work.' % (DEAD,))


def test_the_new_value_is_one_the_writer_accepts():
    '''RED TODAY: 'covered' is outside the enum. Read from the module at test time, so the
    expectation cannot drift from the vocabulary and cannot be widened by this suite.'''
    legal = tuple(sr_mod().DISPOSITIONS)
    value = record().get('disposition')
    assert value in legal, (
        'disposition %r is not in session_review.DISPOSITIONS %s -- scripts/session_review.py:935 '
        'is the vocabulary, and scripts/tests/test_session_review_disposition.py:20 encodes the '
        'same set' % (value, legal))
    assert len(legal) == 5, (
        'DISPOSITIONS is %s: the ratchet at scripts/tests/test_session_review_verification_claims.py:221 '
        'pins five values; a sixth added to legalise the record is the defect, not the fix' % (legal,))


def test_evidence_names_the_three_transcript_rows_the_commit_and_the_handover():
    '''RED TODAY (no 'evidence' key). Three DISTINCT transcript seqs, the commit, the artefact
    and the git command form -- one placeholder string pasted in cannot satisfy all of them.'''
    ev = record().get('evidence')
    assert isinstance(ev, str) and ev.strip(), (
        'the record has no evidence string (found %r). workspace/rca/cea6783d739b.md states the '
        'evidence as: transcript seq %s and git show --stat %s' % (ev, '/'.join(SEQS), SHA))
    missing = [s for s in SEQS if s not in ev]
    assert not missing, (
        'evidence omits transcript seq %r; the RCA names %s: %r' % (missing, '/'.join(SEQS), ev))
    assert SHA in ev, 'evidence must cite the discharge commit %r: %r' % (SHA, ev)
    assert HANDOVER in ev, (
        'the promise was to fill in the handover; evidence must name the artefact %r: %r'
        % (HANDOVER, ev))
    assert 'git show' in ev, (
        'evidence must record the check that shows the handover landed, in the form '
        "'git show --stat %s': %r" % (SHA, ev))


def test_the_handover_claim_agrees_with_the_file_on_disk():
    '''RED TODAY (no evidence). The expectation is COMPUTED from disk at test time, so a fabricated
    evidence string cannot pass, and if the artefact is ever removed the record goes red on its
    own -- a stored expectation the fixer cannot see is the trap this avoids.'''
    ev = str(record().get('evidence') or '')
    assert HANDOVER in ev, 'the claim under test must name %r: %r' % (HANDOVER, ev)
    exists = (ROOT / HANDOVER).is_file()
    says_present = bool(re.search(r'\bpresent\b|\bexists\b', ev, re.I))
    says_absent = bool(re.search(r'\babsent\b|\bdoes not exist\b|\bmissing\b', ev, re.I))
    assert says_present != says_absent, (
        'evidence asserts both present and absent, so it settles nothing: %r' % (ev,))
    assert says_present == exists, (
        'the record says the handover artefact is present=%s; the file on disk says present=%s '
        '-- the claim must match the tree (%s)' % (says_present, exists, HANDOVER))


def test_covered_by_points_at_the_discharging_commit_not_at_a_tracker():
    '''RED TODAY ('t1052'). covered_by's job is the pointer a reader follows: a task id defers the
    instance to a tracker that may never land, a commit sha closes it today.'''
    rec = record()
    value = str(rec.get('covered_by') or '')
    assert value == COVERED_BY, (
        'covered_by is %r; workspace/rca/cea6783d739b.md prescribes %r' % (value, COVERED_BY))
    assert re.fullmatch(r'fixed:[0-9a-f]{7,40}', value) is not None, (
        'covered_by must be the literal form %r (fixed: + hex sha): %r' % (COVERED_BY, value))
    assert OUTCOME_TASK not in value, (
        'covered_by must not keep naming %s: that is the tracker the RCA replaces with the commit'
        % (OUTCOME_TASK,))


def test_the_re_disposition_kept_every_field_a_reader_of_the_record_uses():
    '''Green today, green after: provenance is why the finding is auditable, so adding the
    re-disposition must not erase it. Values are the record's own, read off disk today.'''
    rec = record()
    lost = [f for f in PROVENANCE if f not in rec]
    assert lost == [], 'the re-disposition dropped %r' % (lost,)
    assert rec.get('session') == SESSION, 'session must stay %r' % (SESSION,)
    assert rec.get('completed_at') == COMPLETED_AT, 'completed_at must stay %r' % (COMPLETED_AT,)
    assert rec.get('outcome_task') == OUTCOME_TASK, (
        'outcome_task must stay %r: the re-disposition replaces covered_by, not the history of '
        'what the review matched the promise to' % (OUTCOME_TASK,))
    assert isinstance(rec.get('candidates_consulted'), list) and rec.get('candidates_consulted'), (
        'candidates_consulted must stay the record\'s non-empty list of tasks consulted, found %r'
        % (rec.get('candidates_consulted'),))
    assert isinstance(rec.get('unusable'), list) and rec.get('unusable'), (
        'unusable must stay the record\'s list of unusable model passes (the RCA records one at '
        '2026-10-18T03:44:42Z); a silent close must not erase it')


if __name__ == '__main__':
    _cases = sorted((n, f) for n, f in list(globals().items())
                    if n.startswith('test_') and callable(f))
    _failed = []
    for _name, _fn in _cases:
        try:
            _fn()
            print('PASS ' + _name)
        except AssertionError as _e:
            _failed.append(_name)
            print('FAIL ' + _name + ': ' + str(_e))
        except Exception as _e:
            _failed.append(_name)
            print('ERROR ' + _name + ': ' + type(_e).__name__ + ': ' + _e)
    print('%d/%d passed' % (len(_cases) - len(_failed), len(_cases)))
    sys.exit(1 if _failed else 0)
