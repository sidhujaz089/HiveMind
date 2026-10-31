#!/usr/bin/env python3
# t1439 -- the deferral record for ef053d31949a must say what the transcript says.
#
# The record asserts, and has asserted since 2026-10-08T16:15:03Z, that the deferred
# check was "never mentioned again; no covering task found". The transcript refutes it:
# seq 946-947 (07:25:09Z, 85 s after the deferral at 07:23:43Z) built a populated
# bundle, ran --verify against the patched scripts/backup_corpora.py, and printed
# "Backup verified: every file present and byte-identical." with rc=0. Timeline:
# workspace/rca/ef053d31949a.md:17-27; the guard is on disk at backup_corpora.py:349-351.
#
# RED ON MAIN for the missing change and only for it: on main the record's disposition is
# "covered", it has no "evidence" key, no "residue_note", and why_missed still carries
# "never mentioned again". GREEN when the record carries disposition
# "resolved_in_session", covered_by "t1139", the six seq anchors 937/938/939/946/947/949
# each saying what it proves, and a residue note naming the NAS bundle, t1025 and t730.
#
# Read-only: no writes, no subprocess, no model call, no live server, nothing left running.
# Run: python3 scripts/tests/test_finding_ef053d31949a_discharge.py
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FINDINGS = ROOT / 'workspace' / 'session-reviews' / 'findings'
# The record lives in findings/done/ on this tree (verified: findings/ holds 29 json
# files, none named ef053d31949a; findings/done/ holds 734 including it). The RCA's
# fix text names findings/ef053d31949a.json, so the lookup follows the record if the
# pipeline relocates it: a move shows as a passing run, a deletion as a loud failure.
CANDIDATES = [FINDINGS / 'done' / 'ef053d31949a.json', FINDINGS / 'ef053d31949a.json']

# The six transcript seqs the RCA names. Six DISTINCT integers: an oracle that accepted
# the same seq six times would pass a build that pasted one placeholder six times.
SEQS = [937, 938, 939, 946, 947, 949]
RESIDUE_TARGETS = ['t1025', 't730']
ID_ROW = re.compile(r'^t\d+$')


def record():
    for path in CANDIDATES:
        if path.is_file():
            with path.open(encoding='utf-8') as fh:
                return json.load(fh)
    raise AssertionError('finding record at neither %s' % ' nor '.join(str(p) for p in CANDIDATES))


def anchors(rec):
    ev = rec.get('evidence')
    assert isinstance(ev, list) and ev, 'evidence: expected a non-empty list, got %r' % (ev,)
    for entry in ev:
        assert isinstance(entry, dict), 'evidence entry is not an object: %r' % (entry,)
        assert 'seq' in entry and 'proves' in entry, 'evidence entry lacks seq/proves: %r' % (entry,)
        assert isinstance(entry['seq'], int), 'evidence seq must be an int, got %r' % (entry['seq'],)
        assert isinstance(entry['proves'], str) and entry['proves'].strip(), 'evidence entry has an empty proves: %r' % (entry,)
    return ev


def test_record_is_the_right_record():
    # A build that replaces the record with a stub passes nothing: identity fields pinned.
    rec = record()
    assert rec.get('key') == 'ef053d31949a', 'key: %r' % (rec.get('key'),)
    assert rec.get('session') == 'aeef1f57-5a5c-47b9-9d13-6a9c6fc9b373', 'session: %r' % (rec.get('session'),)
    assert rec.get('kind') == 'deferral', 'kind: %r' % (rec.get('kind'),)


def test_disposition_is_resolved_in_session():
    rec = record()
    got = rec.get('disposition')
    assert got == 'resolved_in_session', 'disposition: expected resolved_in_session, got %r' % (got,)
    for stale in ('deferred', 'open', 'covered', 'resolved'):
        assert got != stale, 'disposition is %r, not the resolved_in_session the RCA asks for' % (got,)
    assert rec.get('covered_by') == 't1139', 'covered_by: expected t1139, got %r' % (rec.get('covered_by'),)


def test_the_false_claim_is_gone():
    # The load-bearing assertion: the record may not assert a transcript fact the
    # transcript refutes. Checked over the whole serialised record, so the claim cannot
    # survive by moving keys around.
    blob = json.dumps(record())
    assert 'never mentioned again' not in blob, 'the record still asserts the claim refuted by seq 946-947'


def test_evidence_anchors_are_the_six_real_seqs():
    got = sorted(e['seq'] for e in anchors(record()))
    assert got == sorted(SEQS), 'evidence seqs: expected %s, got %s' % (sorted(SEQS), got)
    assert len(set(got)) == len(got), 'evidence seqs repeat a value: %s' % (got,)


def test_each_anchor_says_what_it_proves():
    entries = anchors(record())
    texts = [e['proves'] for e in entries]
    assert len(set(t.strip() for t in texts)) == len(texts), 'anchors share one placeholder proves-text: %r' % (texts,)
    blob = ' '.join(texts).lower()
    assert 'rc=0' in blob, 'no anchor records the rc=0 the verification ran with: %r' % (texts,)
    assert 'backup verified' in blob, 'no anchor records the transcript line Backup verified: %r' % (texts,)


def test_residue_note_routes_the_literal_ask():
    rec = record()
    note = rec.get('residue_note')
    assert isinstance(note, str) and note.strip(), 'residue_note: expected a non-empty string, got %r' % (note,)
    low = note.lower()
    assert 'nas' in low, 'residue_note does not name the NAS bundle the literal ask was about: %r' % (note,)
    for tid in RESIDUE_TARGETS:
        assert tid in note, 'residue_note does not route the residue to %s: %r' % (tid, note)
    assert re.search(r'\bnot\b', low), 'residue_note must state the literal ask was NOT run in-session: %r' % (note,)


def test_the_candidate_array_is_not_truncated():
    # never-truncate-a-record: the 949-entry candidates_consulted array is evidence of
    # how wide the reviewer looked. A build that tidies it away fails here.
    arr = record().get('candidates_consulted')
    assert isinstance(arr, list), 'candidates_consulted is not a list'
    assert len(arr) >= 900, 'candidates_consulted truncated: %d entries, 949 on main' % (len(arr),)
    bad = [x for x in arr if not isinstance(x, str) or not ID_ROW.match(x)]
    assert not bad, 'candidates_consulted entries that are not task ids: %r' % (bad[:5],)


def test_outcome_fields_stay_t1139s():
    rec = record()
    assert rec.get('outcome_task') == 't1139', 'outcome_task: %r' % (rec.get('outcome_task'),)
    assert rec.get('outcome_status') == 'doing', 'outcome_status: %r (t1139 owns it, not this task)' % (rec.get('outcome_status'),)


if __name__ == '__main__':
    _tests = sorted((v for _k, v in list(globals().items())
                     if _k.startswith('test_') and callable(v)), key=lambda f: f.__name__)
    _failed = 0
    for _fn in _tests:
        try:
            _fn()
            print('PASS %s' % _fn.__name__)
        except AssertionError as _exc:
            _failed += 1
            print('FAIL %s: %s' % (_fn.__name__, _exc))
    print('%d/%d passed' % (len(_tests) - _failed, len(_tests)))
    sys.exit(1 if _failed else 0)
