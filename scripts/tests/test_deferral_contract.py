"""Oracle for t607 -- the deferral contract's five required fields.

Task: workspace/tasks/t607--add-required-fields-to-deferral-kind-in-failure.md
RCA:  workspace/rca/61fe8d8db367.md, workspace/rca/5eecf63e0e33.md,
      workspace/rca/80adbb22286b.md, workspace/rca/6ed92e74f7ed

The contract, as written by Jas at t607 md:18: a deferral record must carry
tracked_as, owner, verify_command, decay_test, intended_state, and a record
missing any of the five is REFUSED. tracked_as is a tracker number (tNNN) or an
explicit UNTRACKED carrying a reason.

The record shape is the one scripts/session_review.py already mints (read of its
lines 347-350 and 773-777): kind, quote, where, why_missed, covered_by, and the
disposition bucket at line 792. A deferral is kind == "deferral".

Red on main for one reason: the four names this suite imports from session_review
(DEFERRAL_FIELDS, deferral_violations, refuse_if_deficient, DeferralContractError)
are not defined there yet. No I/O, no subprocess, no model call, no live state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import session_review as sr


# The five names, verbatim from the task text (t607 md:18). They are the contract:
# a validator that checks one of them is not a validator of the contract.
FIVE = ("tracked_as", "owner", "verify_command", "decay_test", "intended_state")


def complete_record():
    """A deferral record that satisfies the contract. The values are the real ones
    from the Sep-12 timer-pull deferral (rca 61fe8d8db367), so a stub that ignores
    the values passes nothing. kind is the value session_review mints at :347."""
    return {
        "kind": "deferral",
        "quote": "crontab ~/crontab.new if you still want them pulled.",
        "tracked_as": "t607",
        "owner": "claude-code",
        "verify_command": "systemctl --user list-timers",
        "decay_test": "scripts/tests/test_deferral_contract.py",
        "intended_state": "no timer-gemma/timer-q16 in `systemctl --user list-timers`",
    }


def test_module_declares_the_five_fields():
    assert tuple(sr.DEFERRAL_FIELDS) == FIVE, (
        "field list drifted from the contract at t607 md:18: %r" % (sr.DEFERRAL_FIELDS,))


def test_complete_record_is_accepted():
    problems = sr.deferral_violations(complete_record())
    assert problems == [], "a complete record was refused: %r" % (problems,)


def test_each_of_the_five_fields_is_required_one_at_a_time():
    """A validator that hard-codes one field, or that reports every field on any
    gap, passes a shape-only test. This one cannot: each removal must be refused
    AND named, and must not bring the others with it."""
    for field in FIVE:
        rec = complete_record()
        del rec[field]
        problems = sr.deferral_violations(rec)
        assert problems, "a record with '%s' removed was accepted" % field
        assert len(problems) == 1, (
            "removing only '%s' produced %d problems: %r" % (field, len(problems), problems))
        assert field in problems[0], (
            "the report does not say WHICH field is missing: %r" % (problems,))


def test_blank_value_counts_as_missing_not_as_present():
    rec = complete_record()
    rec["owner"] = "   "
    problems = sr.deferral_violations(rec)
    assert len(problems) == 1 and "owner" in problems[0], (
        "a blank owner is not an owner: %r" % (problems,))


def test_non_string_value_counts_as_missing_not_as_present():
    rec = complete_record()
    rec["decay_test"] = 72
    problems = sr.deferral_violations(rec)
    assert len(problems) == 1 and "decay_test" in problems[0], (
        "the number 72 was accepted as a decay-test fixture: %r" % (problems,))


def test_tracked_as_untracked_needs_a_reason():
    rec = complete_record()
    rec["tracked_as"] = "UNTRACKED"
    problems = sr.deferral_violations(rec)
    assert len(problems) == 1 and "tracked_as" in problems[0], (
        "bare UNTRACKED was accepted -- the RCA's finding is exactly a deferral "
        "with no tracker and no reason: %r" % (problems,))

    rec2 = complete_record()
    rec2["tracked_as"] = "UNTRACKED: no tNNN exists for the timer retirement"
    assert sr.deferral_violations(rec2) == [], (
        "UNTRACKED carrying a reason must be accepted (t607 md:18)")


def test_tracked_as_must_be_a_tracker_number_not_prose():
    rec = complete_record()
    rec["tracked_as"] = "yes"
    problems = sr.deferral_violations(rec)
    assert len(problems) == 1 and "tracked_as" in problems[0], (
        "'yes' was accepted as a tracker: %r" % (problems,))


def test_extra_keys_are_not_refused():
    """Negative assertion: the record carries more than the contract. Refusing a
    superset would break every caller the wiring feeds into this validator."""
    rec = complete_record()
    rec["note"] = "the timers are still installed and firing"
    assert sr.deferral_violations(rec) == [], (
        "a record with extra keys was refused: %r" % (sr.deferral_violations(rec),))


def test_only_a_deferral_record_is_gated():
    """Negative assertion, and the one the wiring needs: the contract is on the
    deferral kind. A finding of any other kind session_review mints (:347) carries
    none of the five fields by design, so gating it would refuse every finding."""
    for kind in ("unresolved", "off-flow", "for-jas"):
        rec = {"kind": kind,
               "quote": "the timers are still installed and firing",
               "where": "assistant, near the end",
               "why_missed": "never filed",
               "covered_by": ""}
        assert sr.deferral_violations(rec) == [], (
            "a %s finding was asked for the deferral fields: %r"
            % (kind, sr.deferral_violations(rec)))


def test_a_record_shaped_by_the_current_minter_is_refused():
    """The real artefact: the keys session_review.py tells the model to emit
    (:347-350) and builds in code (:773-777). A deferral built that way carries
    none of the five, so the report must name all five -- the whole reason this
    validator exists."""
    rec = {"kind": "deferral",
           "quote": "crontab ~/crontab.new if you still want them pulled.",
           "where": "assistant, pending-items list near end of session",
           "why_missed": "no task number was given for it",
           "covered_by": "",
           "disposition": "deferred"}
    report = " ".join(sr.deferral_violations(rec))
    assert report, "a deferral with none of the five fields was accepted"
    for field in FIVE:
        assert field in report, "the report omits '%s': %r" % (field, report)


def test_non_mapping_input_is_reported_not_swallowed():
    raised = None
    try:
        sr.deferral_violations("pull the timers")
    except TypeError as exc:
        raised = str(exc)
    assert raised is not None, "a non-mapping record must raise TypeError, not pass silently"
    assert "str" in raised, "the TypeError must record what it was given: %r" % (raised,)


def test_refuse_if_deficient_raises_and_names_the_missing_field():
    rec = complete_record()
    del rec["verify_command"]
    raised = None
    try:
        sr.refuse_if_deficient(rec)
    except sr.DeferralContractError as exc:
        raised = str(exc)
    assert raised is not None, "refuse_if_deficient accepted a record with no verify_command"
    assert "verify_command" in raised, "the refusal must name the missing field: %r" % (raised,)

    assert sr.refuse_if_deficient(complete_record()) is None, (
        "a complete record must pass the gate")


def _run():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except AssertionError as exc:
            failed.append(name)
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:
            failed.append(name)
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d passed" % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
