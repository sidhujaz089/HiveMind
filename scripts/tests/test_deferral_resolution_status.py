#!/usr/bin/env python3
'''t1266: a deferral finding must carry resolution_status + resolution_evidence.

The RCA (workspace/rca/e0e52b65b4bd.md) found a correctly-resolved deferral is
indistinguishable from a dropped action because the finding contract has no
resolution state. This pins the contract in scripts/session_review.py:
  - resolution_status is required and must be one of OPEN / MET-IN-SESSION /
    MET-LATER / NOT-MET;
  - resolution_evidence is required (a transcript line, commit sha, or STATUS
    line showing where the resolution is tracked);
  - the canonical miss -- OPEN with no evidence -- is rejected.
'''
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))

import session_review as sr

_T607 = {
    'tracked_as': 't1266',
    'owner': 'production',
    'verify_command': 'echo resolution-contract-check',
    'decay_test': 'python3 scripts/tests/test_deferral_resolution_status.py',
    'intended_state': 'every kind=deferral finding states how it was resolved',
}


def _rec(**extra):
    return {'kind': 'deferral', **_T607, **extra}


def test_missing_both_fields_is_rejected():
    v = sr.deferral_violations(_rec())
    assert v, 'a deferral with no resolution_status/evidence must be rejected'
    assert any('resolution_status' in x for x in v), v
    assert any('resolution_evidence' in x for x in v), v


def test_open_with_empty_evidence_is_rejected():
    v = sr.deferral_violations(_rec(resolution_status='OPEN', resolution_evidence=''))
    assert v, 'OPEN with empty resolution_evidence must be rejected'
    assert any('resolution_evidence' in x for x in v), v


def test_invalid_status_value_is_rejected():
    v = sr.deferral_violations(
        _rec(resolution_status='NOT-A-REAL-STATUS', resolution_evidence='turn 240'))
    assert v, 'a status outside the enum must be rejected'
    assert any('NOT-A-REAL-STATUS' in x for x in v), v


def test_complete_finding_is_accepted():
    v = sr.deferral_violations(
        _rec(resolution_status='MET-IN-SESSION',
             resolution_evidence='transcript turn 240: keyboard confirmed'))
    assert v == [], v


def test_non_deferral_is_not_gated():
    assert sr.deferral_violations({'kind': 'miss', 'quote': 'x'}) == []


if __name__ == '__main__':
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
            except AssertionError as e:
                failures += 1
                print('FAIL', name, e)
    if failures:
        print(f'{failures} test(s) failed')
        sys.exit(1)
    print('ok: all deferral resolution_status tests passed')
