#!/usr/bin/env python3
"""t777 -- the self-contaminated-measurement class is VALIDATED, not just named.

A count/scan/eval whose population includes the instrument that produced it is a defect
with no place to land: workspace/failure-schema.json has no such class, and
scripts/failure_schema.py has no way to REFUSE a row that states a measurement without
naming how the instrument was removed. (All 88 lines of failure_schema.py were read at
scope time: RULES, classify, append, record_failure -- no validator.)

Red on main for exactly that: validate_row does not exist on the module.

No live state: this suite writes no files, starts no process, calls no model, and opens no
network connection. validate_row is a pure function over a dict; every row here is a literal.
"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MODULE = REPO / "scripts" / "failure_schema.py"
CLASS = "self-contaminated-measurement"


def load_module():
    spec = importlib.util.spec_from_file_location("fs_under_test", MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def count_row(**over):
    """A count produced by a probe whose shell is inside the counted set -- the
    410fbd3665d0 / 0541626c2002 shape: a /proc scan reporting '1 left' that was the
    assistant's own shell, whose cmdline contained the string `probe.py`."""
    row = {
        "class": CLASS,
        "cause_category": "content",
        "population_kind": "count",
        "measured_population": {
            "source": "ps aux",
            "selector": "grep -c probe.py",
        },
        "pattern": "probe.py",
        "pattern_provenance": "literal",
        "self_exclusion": "grep -v $$",
    }
    row.update(over)
    return row


def eval_row(**over):
    """The 46e20534a715 shape: eval cases authored by the instrument being measured, after
    it was built -- a ground-truth population the measurer wrote."""
    row = {
        "class": CLASS,
        "cause_category": "content",
        "population_kind": "eval",
        "measured_population": {
            "source": "scripts/evals/memory_cases.jsonl",
            "selector": "the three cases written for the memory.py eval",
        },
        "case_provenance": "authored-after-implementation",
        "authored_from_sha": "9ddb25fc",
        "authored_by": "claude-6cc34e3b",
        "approved_by": "claude-6cc34e3b",
        "self_exclusion": "scripts/evals/memory_cases.jsonl:1",
    }
    row.update(over)
    return row


def names(problems):
    return " | ".join(str(p) for p in problems)


def test_module_has_validate_row():
    mod = load_module()
    assert hasattr(mod, "validate_row"), (
        "validate_row is missing from scripts/failure_schema.py -- t777's "
        "self-contaminated-measurement class has no validator, so a defect row that "
        "states a count with no self_exclusion validates clean today."
    )


def test_count_row_stating_a_count_with_no_self_exclusion_is_refused():
    """The task's red fixture: a defect row stating a count with NO self_exclusion FAILS."""
    mod = load_module()
    row = count_row()
    del row["self_exclusion"]
    problems = mod.validate_row(row)
    assert isinstance(problems, list), "validate_row must return a list, got %r" % (problems,)
    assert problems, (
        "a self-contaminated-measurement row that states a count with no self_exclusion "
        "validated clean -- that is the defect t777 exists to close (rca/0541626c2002.md:72, "
        "rca/410fbd3665d0.md:75)."
    )
    assert "self_exclusion" in names(problems), (
        "the refusal must name the field it wants, not just say the row is bad: %r" % (problems,)
    )


def test_compliant_count_row_produces_no_problems():
    """NEGATIVE assertion: a row that does name a resolvable exclusion must be accepted.
    A validator that flags every row gets --no-verify'd within a week."""
    mod = load_module()
    assert mod.validate_row(count_row()) == [], (
        "a fully-specified count row (source, selector, literal provenance, 'grep -v $$') "
        "must validate clean, got %r" % (mod.validate_row(count_row()),)
    )


def test_self_exclusion_must_be_a_resolvable_artefact_not_prose():
    """The artefact shapes every RCA asks for, and the one thing that must NOT pass:
    a sentence saying the instrument was kept out."""
    mod = load_module()
    for artefact in ("grep -v $$", "grep -v $PPID", "--exclude-self", "[p]robe.py",
                     "excluded own pid 4102", "scripts/check_stale_services.py:213"):
        assert mod.validate_row(count_row(self_exclusion=artefact)) == [], (
            "a resolvable exclusion artefact must be accepted: %r" % (artefact,)
        )
    for prose in ("", "careful", "the own shell was excluded", "N/A", None):
        problems = mod.validate_row(count_row(self_exclusion=prose))
        assert problems and "self_exclusion" in names(problems), (
            "self_exclusion=%r is prose, not an artefact -- it must be refused with a "
            "problem naming the field, got %r" % (prose, problems)
        )


def test_measured_population_must_name_source_and_selector():
    mod = load_module()
    for bad in ({}, {"source": "ps aux"}, {"selector": "grep -c probe.py"}, "ps aux", ""):
        problems = mod.validate_row(count_row(measured_population=bad))
        assert problems and "measured_population" in names(problems), (
            "measured_population=%r does not name both the source and the selector; it must "
            "be refused with a problem naming the field, got %r" % (bad, problems)
        )
    assert mod.validate_row(count_row()) == []


def test_pattern_provenance_must_be_literal_or_runtime():
    """A pattern built at runtime is legal to RECORD (selfmatch.py reports runtime-built
    patterns UNKNOWN, never a pass) -- it is not legal to leave unstated."""
    mod = load_module()
    assert mod.validate_row(count_row(pattern_provenance="runtime")) == []
    for bad in ("runtime-built", "invented", "", None, "literal "):
        problems = mod.validate_row(count_row(pattern_provenance=bad))
        assert problems and "pattern_provenance" in names(problems), (
            "pattern_provenance=%r is not 'literal' or 'runtime'; it must be refused with a "
            "problem naming the field, got %r" % (bad, problems)
        )


def test_eval_row_approved_by_the_instrument_that_authored_it_is_refused():
    """46e20534a715: cases I invented after building the thing, approved by me.
    approved_by == authored_by must FAIL (it passes today -- there is no validator)."""
    mod = load_module()
    problems = mod.validate_row(eval_row())
    assert problems and "approved_by" in names(problems), (
        "an eval row authored and approved by the same instrument must be refused with a "
        "problem naming approved_by, got %r" % (problems,)
    )


def test_eval_row_needs_case_provenance_and_a_distinct_approver():
    mod = load_module()
    ok = eval_row(approved_by="claude-9ddb25fc")
    assert mod.validate_row(ok) == [], (
        "a ground-truth population authored by one instrument and approved by another must "
        "validate clean, got %r" % (mod.validate_row(ok),)
    )
    for bad in ("", None, "written-later", "authored after the build"):
        problems = mod.validate_row(eval_row(approved_by="claude-9ddb25fc", case_provenance=bad))
        assert problems and "case_provenance" in names(problems), (
            "case_provenance=%r is not 'authored-before' or 'authored-after-implementation'; "
            "it must be refused with a problem naming the field, got %r" % (bad, problems)
        )
    problems = mod.validate_row(eval_row(approved_by="claude-9ddb25fc", authored_by=""))
    assert problems and "authored_by" in names(problems), (
        "a ground-truth population with no authored_by is unattributable, got %r" % (problems,)
    )


def test_population_kind_is_required_for_this_class():
    """The field that says WHICH population: count/scan/listing (needs pattern_provenance)
    vs eval (needs case_provenance + authorship). Without it the class cannot say what shape
    the contamination took, and the required-field set is unfalsifiable."""
    mod = load_module()
    row = count_row()
    del row["population_kind"]
    problems = mod.validate_row(row)
    assert problems and "population_kind" in names(problems), (
        "a self-contaminated-measurement row with no population_kind must be refused with a "
        "problem naming the field, got %r" % (problems,)
    )
    for bad in ("", None, "measurement", "scan "):
        problems = mod.validate_row(count_row(population_kind=bad))
        assert problems and "population_kind" in names(problems), (
            "population_kind=%r is not one of count/scan/listing/eval; got %r" % (bad, problems)
        )


def test_rows_of_other_classes_are_not_validated_by_this_class():
    """NEGATIVE assertion and the bypass guard: the appender writes rows of ten other
    classes today (failure_schema.py RULES:29-38). A validator that reports on them is a
    validator that gets switched off."""
    mod = load_module()
    for cls in ("harness-edit-application", "tests-failed", "window-full", "unclassified"):
        problems = mod.validate_row({"class": cls, "cause_category": "content",
                                     "instances": [{"at": "2026-10-08", "chunk": "p1"}]})
        assert problems == [], (
            "validate_row must return [] for class %r -- it validates the new class only, "
            "got %r" % (cls, problems)
        )


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]


if __name__ == "__main__":
    failures = 0
    for fn in TESTS:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except AssertionError as exc:
            failures += 1
            print("FAIL %s: %s" % (fn.__name__, exc))
        except Exception as exc:  # a crash is a finding, printed whole -- never swallowed
            failures += 1
            print("ERROR %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
    print("%d test(s), %d failure(s)" % (len(TESTS), failures))
    raise SystemExit(1 if failures else 0)
