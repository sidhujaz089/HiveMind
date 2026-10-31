#!/usr/bin/env python3
"""t1095: a deferral finding dispositioned 'unresolved' must carry implementation_evidence.

Ground truth -- workspace/rca/b0784ca794ef.md: the finding recorded kind="deferral",
disposition="unresolved", covered_by="" for a feature the RCA proves was BUILT the same
evening (commit bda1b7aa, scripts/memory.py, workspace/memory.jsonl, MCP append/recall,
done task t165). The deferral path has no 'already implemented' category, so a built
feature fell into the only bucket it had -- the 'a missing category is a misattribution
waiting to be automated' class.

The contract lives in the validator scripts/session_review.py already ships
(DEFERRAL_FIELDS, deferral_violations, refuse_if_deficient, DeferralContractError).
The write gate is already wired: scripts/tests/test_t1048_deferral_write_gate.py:92 asserts
"_keep() must consult refuse_if_deficient() before writing", so extending the validator
inherits enforcement.

RED ON MAIN, for the stated reason: deferral_violations() today checks only the five t607
fields (tracked_as, owner, verify_command, decay_test, intended_state) and knows nothing
named implementation_evidence, so the first assertion that fires is
test_missing_evidence_is_a_violation, saying 'no violation names implementation_evidence'.

Hermetic: pure validator calls. No writes, no subprocess, no network, no model, no temp dirs.
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import session_review as sr  # noqa: E402

FIELD = "implementation_evidence"
_ABSENT = object()


def record(**over):
    """A deferral finding shaped like workspace/rca/b0784ca794ef, already carrying the five
    t607 fields deferral_violations enforces -- so the only violation left is the new field."""
    rec = {
        "kind": "deferral",
        "disposition": "unresolved",
        "quote": "we can make something akin to the corpus, for our local models so they can "
                 "look up lessons and learn from past sessions.",
        "what": "local-model lesson store that appends like chat",
        "covered_by": "",
        "tracked_as": "UNTRACKED: schema fix, no open task owns it",
        "owner": "session-review",
        "verify_command": "python3 scripts/tests/test_deferral_implementation_evidence.py",
        "decay_test": "this suite goes red when a violation stops naming the field",
        "intended_state": "no deferral reaches 'unresolved' without stated evidence",
    }
    for key, value in over.items():
        if value is _ABSENT:
            rec.pop(key, None)
        else:
            rec[key] = value
    return rec


def evidence_violations(rec):
    """The part of the validator's verdict that concerns the new field."""
    return [v for v in sr.deferral_violations(rec) if FIELD in str(v)]


def test_missing_evidence_is_a_violation():
    v = evidence_violations(record())
    assert v, ("no violation names %s for a deferral dispositioned 'unresolved': %r"
               % (FIELD, sr.deferral_violations(record())))
    assert len(v) == 1, "expected exactly one %s violation, got %r" % (FIELD, v)
    assert FIELD in v[0], "the violation must name the field it rejects: %r" % (v,)


def test_the_five_t607_fields_are_not_broken_by_the_new_one():
    # A record carrying all five must gain NO violation about them: the new field is ADDED to
    # the contract, not substituted for it.
    others = [v for v in sr.deferral_violations(record()) if FIELD not in str(v)]
    assert not others, ("a deferral carrying all five t607 fields reported extra violations: "
                        "%r" % (others,))


def test_empty_evidence_is_a_violation():
    for value in ("", "   ", "\t\n"):
        v = evidence_violations(record(**{FIELD: value}))
        assert v, "an empty %s=%r passed as evidence for an unresolved deferral" % (FIELD, value)
        assert len(v) == 1, "expected one violation for %r, got %r" % (value, v)
        assert FIELD in v[0], "the violation for %r must name %s: %r" % (value, FIELD, v)


def test_a_commit_hash_is_accepted():
    # bda1b7aa is the real commit the RCA cites as building the feature.
    rec = record(**{FIELD: "bda1b7aa"})
    assert sr.deferral_violations(rec) == [], (
        "a commit hash is legal implementation evidence; got %r" % (sr.deferral_violations(rec),))


def test_a_done_task_id_is_accepted():
    # t165 is the real done task the RCA cites as the local-model recall/append half.
    rec = record(**{FIELD: "t165"})
    assert sr.deferral_violations(rec) == [], (
        "a done-task id is legal implementation evidence; got %r" % (sr.deferral_violations(rec),))


def test_none_plus_the_commands_searched_is_accepted():
    # The RCA's own recorder set (section 4). A reviewer that ran the recorders and found
    # nothing must be able to say so, with the commands it ran.
    rec = record(**{FIELD: "none; searched: git log --since=2026-08-26T20:27Z -- "
                           "scripts/memory.py projects/local-inference/coder.py ; "
                           "ls -la workspace/memory.jsonl ; ls workspace/tasks/done/ | "
                           "grep t165"})
    assert sr.deferral_violations(rec) == [], (
        "an explicit none naming the commands the reviewer ran is legal evidence; got %r"
        % (sr.deferral_violations(rec),))


def test_prose_that_did_not_look_is_rejected():
    # 'I did not check' is not a legal value (RCA section 5, Rule 5 rung 1). Six DISTINCT
    # strings, so one lenient rule cannot accept them all by accident. A bare 'none' is the
    # same absence of search as 'I did not check': no commands, no evidence.
    for value in ("I did not check", "not implemented", "n/a", "none",
                  "checked nothing", "will do later"):
        v = evidence_violations(record(**{FIELD: value}))
        assert v, "%r was accepted as evidence for an unresolved deferral" % (value,)
        assert FIELD in v[0], "the violation for %r must name %s: %r" % (value, FIELD, v)


def test_the_field_is_not_required_when_the_finding_is_not_unresolved():
    # The bypass guard. 126 finding JSONs sit in workspace/session-reviews/findings/; a gate
    # that fires on every deferral disposition is routed around within a week, which is worth
    # less than no gate. Evidence is required ONLY on disposition='unresolved'.
    for disposition in ("deferred", "resolved-in-session", "refuted", "for-jas"):
        rec = record(disposition=disposition)
        rec.pop(FIELD, None)
        assert not evidence_violations(rec), (
            "the field is required only on disposition='unresolved'; it flagged %r" % (rec,))


def test_a_non_deferral_kind_is_untouched():
    # 'unresolved' is also a triage KIND the EXTRACT prompt mints; deferral_violations keeps
    # returning [] for non-deferral kinds. Pinning this stops the rule being widened to every
    # record shape, which is the over-broad build that gets bypassed.
    rec = record(kind="unresolved")
    rec.pop(FIELD, None)
    assert sr.deferral_violations(rec) == [], (
        "deferral_violations returns [] for a non-deferral kind; got %r"
        % (sr.deferral_violations(rec),))


def test_the_mapping_check_survives():
    try:
        sr.deferral_violations("not a mapping")
    except TypeError:
        pass
    else:
        raise AssertionError("deferral_violations must still raise TypeError on a non-mapping")


def test_refuse_if_deficient_raises_naming_the_field():
    # The value must be USED by the gate that blocks the write, not just appear in a list.
    # scripts/tests/test_t1048_deferral_write_gate.py:92 shows _keep() consults this function.
    try:
        sr.refuse_if_deficient(record())
    except sr.DeferralContractError as exc:
        assert FIELD in str(exc), (
            "DeferralContractError must name the field it rejected; got %r" % (str(exc),))
    else:
        raise AssertionError(
            "refuse_if_deficient accepted an unresolved deferral with no implementation_evidence")


def test_refuse_if_deficient_accepts_a_record_that_states_its_evidence():
    assert sr.refuse_if_deficient(record(**{FIELD: "bda1b7aa"})) is None, (
        "refuse_if_deficient must return None for a record carrying legal evidence")


def test_the_five_t607_fields_still_enforce():
    rec = record(**{FIELD: "bda1b7aa"})
    rec.pop("owner")
    v = sr.deferral_violations(rec)
    assert any("owner" in str(x) for x in v), (
        "dropping owner must still be a violation: %r" % (v,))
    assert not [x for x in v if FIELD in str(x)], (
        "a record with legal evidence must not gain a %s violation: %r" % (FIELD, v))


def _main():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print("PASS %s" % name)
        except Exception as exc:  # noqa: BLE001 -- the runner reports, never swallows
            failed.append(name)
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d passed" % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
