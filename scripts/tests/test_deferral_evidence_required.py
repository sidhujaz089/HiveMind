#!/usr/bin/env python3
"""t1255: a 'deferred' finding must carry evidence -- a covered_by citation or a
refuted_by citation. An evidence-free deferred record is refused, so a promise that
dies as a sentence at session end cannot be recorded as settled work
(workspace/rca/dd0ff35b7d64.md; ~121 ownerless deferrals, workspace/rca/e1fd8480bc64.md).

No pytest, no live state, no model, no subprocess: pure function calls.
Run: python3 scripts/tests/test_deferral_evidence_required.py
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]        # scripts/tests -> repo root
TARGET = ROOT / "scripts" / "session_review.py"


def _load():
    spec = importlib.util.spec_from_file_location("sr_under_test", TARGET)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot build an import spec for %s" % TARGET)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SR = _load()

REFUTATION = ("aeef1f57 turn 2807 (2026-09-02T02:08:44Z): I don't have to touch "
              "their tree at all")


def deferral_record(**over):
    """A deferral that satisfies the EXISTING t607 five-field contract, so the ONLY
    thing it lacks is evidence of a cover or a refutation. That keeps every red
    assertion red for the missing rule, not for a missing field."""
    rec = {"kind": "deferral",
           "disposition": "deferred",
           "tracked_as": "t1255",
           "owner": "production",
           "verify_command": "python3 scripts/tests/test_deferral_evidence_required.py",
           "decay_test": "a deferred finding with no covered_by and no refuted_by writes green",
           "intended_state": "every deferred finding names the task that covers it "
                             "or the evidence refuting it"}
    rec.update(over)
    return rec


def _raises(rec):
    """(did_it_refuse, message) as refuse_if_deficient answers. Any other exception
    propagates: a crash and a refusal must not look the same."""
    try:
        SR.refuse_if_deficient(rec)
    except SR.DeferralContractError as exc:
        return True, str(exc)
    return False, ""


def test_evidence_free_deferral_is_refused():
    ok, msg = _raises(deferral_record())
    if not ok:
        raise AssertionError(
            "refuse_if_deficient accepted an evidence-free deferred record (returned "
            "None); a deferral with neither covered_by nor refuted_by must be refused")
    if "covered_by" not in msg:
        raise AssertionError("refusal does not name the missing covered_by: %s" % msg)
    if "refuted_by" not in msg:
        raise AssertionError("refusal does not name the missing refuted_by: %s" % msg)


def test_covered_by_satisfies_the_evidence_rule():
    if SR.refuse_if_deficient(deferral_record(covered_by="t1255")) is not None:
        raise AssertionError(
            "a deferral citing an open task (covered_by='t1255') must be accepted")


def test_refuted_by_satisfies_the_evidence_rule():
    if SR.refuse_if_deficient(deferral_record(refuted_by=REFUTATION)) is not None:
        raise AssertionError(
            "a deferral citing the refuting transcript line must be accepted")


def test_whitespace_covered_by_is_not_evidence():
    ok, msg = _raises(deferral_record(covered_by="   "))
    if not ok:
        raise AssertionError(
            "covered_by with only spaces counted as evidence: it must be refused")
    if "covered_by" not in msg:
        raise AssertionError("refusal does not name covered_by: %s" % msg)


def test_whitespace_refuted_by_is_not_evidence():
    ok, msg = _raises(deferral_record(refuted_by="  \t "))
    if not ok:
        raise AssertionError(
            "refuted_by with only spaces and a tab counted as evidence: it must be refused")
    if "refuted_by" not in msg:
        raise AssertionError("refusal does not name refuted_by: %s" % msg)


def test_deferral_violations_names_the_missing_evidence():
    v = SR.deferral_violations(deferral_record())
    if not isinstance(v, list):
        raise AssertionError("deferral_violations must return a list, got %r" % (v,))
    if not v:
        raise AssertionError(
            "deferral_violations returned [] for an evidence-free deferral: the rule "
            "is not in the pure validator, so nothing that calls it can enforce it")
    joined = "; ".join(str(x) for x in v)
    if "covered_by" not in joined:
        raise AssertionError("violation list does not name covered_by: %s" % joined)
    if "refuted_by" not in joined:
        raise AssertionError("violation list does not name refuted_by: %s" % joined)
    if SR.deferral_violations(deferral_record(covered_by="t1255")) != []:
        raise AssertionError("a deferral with a covered_by citation must report no violations")
    if SR.deferral_violations(deferral_record(refuted_by=REFUTATION)) != []:
        raise AssertionError("a deferral with a refuted_by citation must report no violations")


def test_the_evidence_rule_keys_on_disposition_not_kind():
    # The RCA's cause is the kind-projection in _keep landing disposition='deferred'
    # on a record whose kind is not 'deferral'. A rule gated on kind=='deferral' leaves
    # that path open, so the rule must key on disposition.
    rec = deferral_record(kind="gap", disposition="deferred")
    ok, msg = _raises(rec)
    if not ok:
        raise AssertionError(
            "disposition='deferred' with kind='gap' was accepted: the evidence rule "
            "must key on disposition, not on kind")
    if "covered_by" not in msg:
        raise AssertionError("refusal does not name covered_by: %s" % msg)


def test_the_t607_five_field_contract_still_fires():
    rec = deferral_record()
    del rec["owner"]
    ok, msg = _raises(rec)
    if not ok:
        raise AssertionError(
            "dropping 'owner' was accepted: the existing five-field deferral contract "
            "must survive the new evidence rule")
    if "owner" not in msg:
        raise AssertionError("refusal does not name the missing owner field: %s" % msg)


def test_non_mapping_record_still_raises_type_error():
    for bad in (None, 7, "deferred", ["tracked_as"]):
        try:
            SR.refuse_if_deficient(bad)
        except SR.DeferralContractError:
            raise AssertionError(
                "the non-mapping record %r raised DeferralContractError; it must raise "
                "TypeError, as it does today" % (bad,))
        except TypeError:
            continue
        raise AssertionError("refuse_if_deficient accepted the non-mapping record %r" % (bad,))


def test_records_that_are_not_deferred_are_left_alone():
    for disp in ("unresolved", "resolved-in-session", "for-jas", "refuted"):
        rec = {"kind": "gap", "disposition": disp}
        if SR.refuse_if_deficient(rec) is not None:
            raise AssertionError(
                "a record with disposition=%r and no deferral fields was refused: the "
                "rule must apply only to disposition == 'deferred'" % (disp,))


TESTS = [
    ("evidence_free_deferral_is_refused", test_evidence_free_deferral_is_refused),
    ("covered_by_satisfies_the_evidence_rule", test_covered_by_satisfies_the_evidence_rule),
    ("refuted_by_satisfies_the_evidence_rule", test_refuted_by_satisfies_the_evidence_rule),
    ("whitespace_covered_by_is_not_evidence", test_whitespace_covered_by_is_not_evidence),
    ("whitespace_refuted_by_is_not_evidence", test_whitespace_refuted_by_is_not_evidence),
    ("deferral_violations_names_the_missing_evidence", test_deferral_violations_names_the_missing_evidence),
    ("evidence_rule_keys_on_disposition_not_kind", test_the_evidence_rule_keys_on_disposition_not_kind),
    ("t607_five_field_contract_still_fires", test_the_t607_five_field_contract_still_fires),
    ("non_mapping_record_raises_type_error", test_non_mapping_record_still_raises_type_error),
    ("records_that_are_not_deferred_are_left_alone", test_records_that_are_not_deferred_are_left_alone),
]


def _main():
    failed = 0
    for name, fn in TESTS:
        try:
            fn()
        except Exception as exc:      # recorded whole, with its type: never sliced
            failed += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
        else:
            print("PASS %s" % name)
    print("%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
