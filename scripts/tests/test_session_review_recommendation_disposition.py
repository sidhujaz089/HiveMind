#!/usr/bin/env python3
# t1428 -- every in-session design-recommendation record must carry a disposition.
# Run:  python3 scripts/tests/test_session_review_recommendation_disposition.py
# Exit 0 when every case passes, 1 otherwise. pytest is not installed: __main__ runs the cases.
#
# WHY (RCA e28235500b0d; session d847024a, turn 117 of 160). One assistant turn held a design
# recommendation with TWO sub-points: (a) the bare-word triggers are too noisy -- ADOPTED, live in
# scripts/session_candidates.py:18-50 -- and (b) a claim-type candidate must be checked against a
# LATER turn's line, 'which a model looking at one sentence can't see' -- never built, never
# filed, re-surfaced 13 days later only because a scan re-read the transcript. RCA cause C5:
# nothing REQUIRED each bullet of a design recommendation to be recorded as
# adopted / dropped:<reason> / filed:<tNNN>. A rule stated only in prose is a rule the next
# dispatch may legally ignore (docs/skills/writing-oracles-for-delegation), so the rule IS this
# file.
#
# RED ON MAIN, FOR THIS REASON: scripts/session_review.py defines no recommendation_violations,
# refuse_if_undispositioned or RecommendationContractError. The nearest thing that exists is the
# t607 DEFERRAL contract -- DEFERRAL_FIELDS at :1062, deferral_violations at :1069,
# refuse_if_deficient at :1102 -- a DIFFERENT record with DIFFERENT fields. Case 1 fails on that
# absence; the later cases are skipped with a printed reason, so the red names the missing change
# and is never a traceback (oracle-crash).
#
# WHAT A WRONG BUILD CANNOT DO:
#   * satisfy a presence check -- every case asserts the violations a reader would read, naming
#     the offending value, not that record['disposition'] is truthy;
#   * return a constant -- t1428, t1054 and two DISTINCT reasons are used, so one placeholder
#     string fails the t1054 case (RCA t613 shape 2);
#   * flag everything -- the accepted cases and the non-recommendation cases assert NO violations,
#     so a validator that rejects every record (the gate that gets bypassed inside a week) fails;
#   * raise without saying why -- the exception TEXT is asserted (RCA t613 shape 3).
#
# DECAY TEST (planning-for-sustainment): delete the disposition branch in recommendation_violations
# and cases 2/3 flip to [BAD]; rename the field away from 'disposition' and every case 2/3 label
# names it.

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / 'scripts' / 'session_review.py'
API = ('recommendation_violations', 'refuse_if_undispositioned', 'RecommendationContractError')

PASSED = 0
FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print('  [ok]   ' + label)
    else:
        FAILED += 1
        print('  [BAD]  ' + label + ': got ' + repr(got) + ', want ' + repr(want))


def load_module():
    # Import by PATH, not by package name: the module resolves REPO from its own __file__, so
    # this is cwd-independent, and loading under a name that is not '__main__' keeps any
    # command-line main() at the bottom of session_review.py from firing inside the test.
    spec = importlib.util.spec_from_file_location('session_review_under_test', MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rec(**over):
    # The record RCA e28235500b0d says went undispositioned, in its real shape.
    base = {'kind': 'design-recommendation',
            'turn': 117,
            'quote': 'CLAIM also needs a later line to be checked (killed, not solved)'}
    base.update(over)
    return base


def joined(violations):
    return ' '.join(violations)


def names_field(violations, field='disposition'):
    return any(field in v for v in violations)


def test_api_exists(m):
    for name in API:
        check('scripts/session_review.py defines ' + name, hasattr(m, name), True)


def test_missing_disposition_is_a_violation(m):
    rv = m.recommendation_violations
    check('no disposition key at all -> a violation naming the field',
          names_field(rv(rec())), True)
    check('disposition="" -> a violation naming the field',
          names_field(rv(disposition='')), True)
    check('disposition of whitespace only -> a violation naming the field',
          names_field(rv(disposition='   \t ')), True)
    check('disposition=None -> a violation naming the field',
          names_field(rv(disposition=None)), True)
    check('disposition=True -> a violation naming the field (a bool is not a verdict)',
          names_field(rv(disposition=True)), True)


def test_accepted_values(m):
    rv = m.recommendation_violations
    check('disposition="adopted" -> no violations', rv(rec(disposition='adopted')), [])
    check('adopted with surrounding whitespace -> no violations',
          rv(rec(disposition='  adopted  ')), [])
    check('dropped: with a real reason -> no violations',
          rv(rec(disposition='dropped: out of scope for a one-shot review pass')), [])
    check('dropped: a second, distinct reason -> no violations',
          rv(rec(disposition='dropped: the resolver belongs to t1054')), [])
    check('filed:t1428 -> no violations', rv(rec(disposition='filed:t1428')), [])
    check('filed: t1054 with a space after the colon -> no violations',
          rv(rec(disposition='filed: t1054')), [])


def test_rejected_values(m):
    rv = m.recommendation_violations
    cases = [
        ('bare dropped with no reason', 'dropped'),
        ('dropped: with an empty reason', 'dropped:'),
        ('dropped: with a whitespace reason', 'dropped:   '),
        ('bare filed with no task id', 'filed'),
        ('filed: with an empty id', 'filed:'),
        ('filed: with a placeholder id', 'filed:tNNN'),
        ('filed: with a bare number, no t', 'filed:1428'),
        ('filed: with two ids in one record', 'filed:t1428,t1054'),
        ('filed: with an id plus prose', 'filed:t1428 -- also adopted'),
        ('an invented verdict word', 'later'),
        ('an invented verdict word in capitals', 'ADOPTED'),
        ("the FINDING bucket's word, not the recommendation's", 'deferred'),
        ('an empty string', ''),
    ]
    for label, value in cases:
        v = rv(rec(disposition=value))
        check(label + ' (' + repr(value) + ') -> a violation naming the field',
              names_field(v), True)
        check(label + ' (' + repr(value) + ') -> the violation quotes the offending value',
              value.strip() in joined(v), True)


def test_vocabulary_is_stated(m):
    # A writer who is told only 'invalid' fixes nothing; the message must carry the words.
    v = joined(m.recommendation_violations(rec(disposition='later')))
    for word in ('adopted', 'dropped', 'filed'):
        check('an unknown disposition names the permitted word ' + repr(word),
              word in v, True)


def test_only_recommendation_records_are_checked(m):
    rv = m.recommendation_violations
    check('a deferral finding with no disposition -> no violations',
          rv({'kind': 'deferral', 'quote': 'I will add the CLAIM resolver later'}), [])
    check('an unresolved finding with no disposition -> no violations',
          rv({'kind': 'unresolved', 'quote': 'the gate is under-specified'}), [])
    check('a record with no kind -> no violations', rv({'quote': 'some prose'}), [])
    check('a record with no kind carrying an invalid disposition -> no violations',
          rv({'quote': 'some prose', 'disposition': 'later'}), [])


def test_it_does_not_borrow_the_deferral_contract(m):
    # t607's contract (session_review.py:1062) is a different record. The new field must not
    # silence it, and the recommendation check must not answer to its field names.
    d = {'kind': 'deferral', 'quote': 'CLAIM needs a later line checked',
         'disposition': 'adopted'}
    names = joined(m.deferral_violations(d))
    for field in ('tracked_as', 'owner', 'verify_command', 'decay_test', 'intended_state'):
        check('a deferral with no ' + field + ' is still reported (t607, :1062)',
              field in names, True)
    check('the recommendation contract is not keyed to a deferral record',
          m.recommendation_violations(d), [])


def test_a_non_mapping_is_refused(m):
    # Mirrors deferral_violations at :1071-1072: a list is not a record, and silence on the
    # shape that cannot carry a disposition is how the field stops being enforced.
    raised = False
    try:
        m.recommendation_violations(['not', 'a', 'record'])
    except TypeError:
        raised = True
    check('a list argument raises TypeError', raised, True)


def test_the_record_is_read_not_repaired(m):
    r = rec(disposition='later')
    before = dict(r)
    m.recommendation_violations(r)
    check('the check does not write the disposition it is complaining about', r, before)


def test_the_raiser_carries_the_reasons(m):
    # The reason is the record: the caller has to learn WHICH field and WHICH value.
    message = None
    try:
        m.refuse_if_undispositioned(rec(disposition='dropped:'))
    except m.RecommendationContractError as exc:
        message = str(exc)
    check('refuse_if_undispositioned raises on a dropped with no reason', message is not None, True)
    for needle in ('disposition', 'dropped:', 'filed'):
        check('the exception text names ' + repr(needle), needle in (message or ''), True)
    check('refuse_if_undispositioned returns None on a complete record',
          m.refuse_if_undispositioned(rec(disposition='filed:t1428')), None)
    check('refuse_if_undispositioned returns None on an adopted record',
          m.refuse_if_undispositioned(rec(disposition='adopted')), None)
    check('refuse_if_undispositioned returns None on a deferral record (not its contract)',
          m.refuse_if_undispositioned({'kind': 'deferral'}), None)
    check('RecommendationContractError is catchable as Exception',
          issubclass(m.RecommendationContractError, Exception), True)


CASES = (
    test_missing_disposition_is_a_violation,
    test_accepted_values,
    test_rejected_values,
    test_vocabulary_is_stated,
    test_only_recommendation_records_are_checked,
    test_it_does_not_borrow_the_deferral_contract,
    test_a_non_mapping_is_refused,
    test_the_record_is_read_not_repaired,
    test_the_raiser_carries_the_reasons,
)


def run(fn, m):
    global FAILED
    try:
        fn(m)
    except Exception as exc:  # noqa: BLE001 -- a crash is a finding, printed with its cause
        FAILED += 1
        print('  [BAD]  ' + fn.__name__ + ' raised ' + type(exc).__name__ + ': ' + str(exc))


def main():
    print('t1428: a disposition on every design-recommendation record (' + str(MODULE) + ')')
    if not MODULE.is_file():
        print('  [BAD]  scripts/session_review.py not found at ' + str(MODULE))
        return
    m = load_module()
    test_api_exists(m)
    if FAILED:
        print('  SKIP: the recommendation contract is absent from scripts/session_review.py --\n'
              '        that absence IS the red this task exists to close (t1428).\n'
              '        The remaining cases need the API and are not run.')
        return
    for fn in CASES:
        run(fn, m)
    print('  ' + str(PASSED) + ' passed, ' + str(FAILED) + ' failed')


if __name__ == '__main__':
    main()
    sys.exit(1 if FAILED else 0)
