'''t1398 -- a deferral finding must name its DELIVERABLE.

workspace/rca/bca486be34ba.md: the deferral quoted in the task body (Q4's open review didn't
produce anything, so the combined file isn't ready to send yet) was resolved IN-SESSION -- the
review packet was assembled and sent at 20:12 -- yet the session review recorded it as deferred,
never mentioned again, no covering task found. The cover search keys off the QUOTE alone, so a
deferral whose deliverable was in fact produced has no term to match on and can never resolve
covered. Rung 1 (the schema) is the most upstream fix: require a deliverable on every deferral,
so the cover search has something to look for.

Run: python3 scripts/tests/test_deferral_deliverable_field.py   (no pytest; exits 1 on failure)

RED ON MAIN: deferral_violations() returns [] for a deferral carrying the five fields required
today, because DEFERRAL_FIELDS has no deliverable, and the EXTRACT prompt never asks for one.
Every failure this suite prints is one of those two facts and nothing else.
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
import session_review  # noqa: E402

# Real artefacts, and a DISTINCT value per field, so a build that fills every field with the
# same string cannot pass (oracle-weak: one repeated placeholder).
PACKET = 'workspace/session-reviews/trials/REVIEW-PACKET-007b3f63.md'
GHOST = 'workspace/session-reviews/trials/REVIEW-PACKET-deleted-9f2c17.md'
PROSE = 'the combined review packet for Q4, once all three reviews are in it'

COMPLETE = {
    'kind': 'deferral',
    'quote': "Q4's open review didn't produce anything, so the combined file isn't ready to send yet.",
    'tracked_as': 't1398',
    'owner': 'Frontline (coder)',
    'verify_command': 'python3 scripts/tests/test_deferral_deliverable_field.py',
    'decay_test': 'drop deliverable from DEFERRAL_FIELDS and this suite goes red',
    'intended_state': 'the deferral resolves covered because the packet exists',
}


def _base(**over):
    'A deferral carrying every field the schema required BEFORE t1398, plus over.'
    r = dict(COMPLETE)
    r.update(over)
    return r


def test_deliverable_is_a_required_deferral_field():
    assert 'deliverable' in session_review.DEFERRAL_FIELDS, (
        'DEFERRAL_FIELDS is %r: a deferral can be recorded with no deliverable, which is the '
        't1398 class' % (session_review.DEFERRAL_FIELDS,))


def test_missing_deliverable_is_a_violation():
    r = _base()
    assert 'deliverable' not in r, 'the fixture must model the pre-t1398 record'
    v = session_review.deferral_violations(r)
    assert v, 'a deferral with no deliverable raised no violation: the field is not required'
    assert any('deliverable' in str(x) for x in v), (
        'violations do not name the field the change adds: %r' % (v,))


def test_deliverable_present_makes_the_record_violation_free():
    v = session_review.deferral_violations(_base(deliverable=PACKET))
    assert v == [], 'a deferral naming its deliverable still reports violations: %r' % (v,)


def test_a_prose_deliverable_is_accepted():
    v = session_review.deferral_violations(_base(deliverable=PROSE))
    assert v == [], ('a prose (non-path) deliverable was rejected: %r -- an invented format rule' % (v,))


def test_a_deliverable_that_is_not_on_disk_is_accepted():
    v = session_review.deferral_violations(_base(deliverable=GHOST))
    assert v == [], ('a deliverable whose file does not exist was rejected: %r -- this scope is the '
                     'schema; file-existence belongs to the cover-search half' % (v,))


def test_empty_deliverable_is_a_violation():
    v = session_review.deferral_violations(_base(deliverable='   '))
    assert v and any('deliverable' in str(x) for x in v), (
        'a blank deliverable was accepted: %r -- a deferral can state no deliverable silently' % (v,))


def test_non_string_deliverable_is_a_violation():
    v = session_review.deferral_violations(_base(deliverable=1398))
    assert v and any('deliverable' in str(x) for x in v), (
        'a non-string deliverable was accepted: %r' % (v,))


def test_the_five_existing_fields_are_still_required():
    r = _base(deliverable=PACKET)
    del r['owner']
    v = session_review.deferral_violations(r)
    assert v and any('owner' in str(x) for x in v), (
        'dropping owner from a deferral that carries a deliverable raised no owner violation: %r' % (v,))


def test_non_deferral_kinds_are_untouched():
    for kind in ('unresolved', 'off-flow', 'for-jas'):
        v = session_review.deferral_violations({'kind': kind, 'quote': 'later, after this lands'})
        assert v == [], 'kind=%s now carries deferral requirements: %r' % (kind, v)
    assert session_review.deferral_violations({}) == [], 'an empty record must not be a deferral'


def test_deferred_record_without_deliverable_is_refused():
    try:
        err = session_review.refuse_if_deficient(_base())
    except session_review.DeferralContractError as exc:
        assert 'deliverable' in str(exc), 'DeferralContractError does not name deliverable: %s' % exc
    else:
        raise AssertionError(
            'refuse_if_deficient accepted a deferral with no deliverable (returned %r) -- the t1398 '
            'class is still statable' % (err,))
    assert session_review.refuse_if_deficient(_base(deliverable=PACKET)) is None, (
        'refuse_if_deficient did not return None for a deferral that names its deliverable')


def test_the_extraction_prompt_asks_for_the_deliverable():
    ex = session_review.EXTRACT
    assert 'deliverable' in ex, (
        'the EXTRACT prompt never asks for a deliverable, so every deferral arrives without the '
        'field this suite makes mandatory')
    assert ex.count('deliverable') >= 2, (
        "'deliverable' appears %d time(s) in EXTRACT: it must be in the (a) deferral definition AND "
        'in the JSON list the reviewer is told to end with, or the model will not fill it'
        % ex.count('deliverable'))


TESTS = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]


def main():
    failed = []
    for t in TESTS:
        try:
            t()
            print('PASS %s' % t.__name__)
        except Exception as e:  # noqa: BLE001 -- a crash in a test is that test failing, said
            failed.append(t.__name__)
            print('FAIL %s: %s: %s' % (t.__name__, type(e).__name__, e))
    print('%d/%d passed' % (len(TESTS) - len(failed), len(TESTS)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
