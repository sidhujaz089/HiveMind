#!/usr/bin/env python3
"""t1091: scope_lint must refuse a brief that edits a SKILL.md with no observed_failure_evidence.

Red on main: scope_lint.static() has no skill-evidence check, so every "is refused" assertion
below finds no "brief-skill-evidence" finding, and CHECK_NAMES has no such class. The two
"is accepted" assertions pass on main; they are the over-fire guards that keep the gate cheap
enough to survive (a gate that fires on every brief is bypassed inside a week).

    python3 scripts/tests/test_scope_lint_skill_evidence.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import scope_lint  # scripts/scope_lint.py -- the file under test, imported from the path it lives on

SKILL_A = "docs/skills/briefing-the-local-model/SKILL.md"
SKILL_B = "docs/skills/writing-oracles-for-delegation/SKILL.md"
NOT_A_SKILL = "scripts/scope_lint.py"
NOT_SKILL_MD = "docs/skills/briefing-the-local-model/NOTES.md"
GOOD_EVIDENCE = ("t1091 (RCA ac7ac8636e39): three post-session edits to briefing-the-local-model "
                 "(a4eaa838, 6b93b546, 8d3fd873) shipped with no dispatch citation; the guard "
                 "promised at turn 202 was never written")


def _impl(path, **extra):
    """A minimal implementation chunk pointed at `path`. static() reads a file's existence, its
    symbols and its cited lines, so a chunk needs nothing more than real paths to be lintable."""
    chunk = {"kind": "implementation", "file": path,
             "target": "scripts/tests/test_bridge.py",
             "single_change": "fixture", "what_is_wrong": "fixture",
             "done_when": "python3 scripts/tests/test_bridge.py exits 0",
             "related_entries": ["t1091"]}
    chunk.update(extra)
    return chunk


def _hits(chunks):
    return [f for f in scope_lint.static(chunks, ROOT) if f[0] == "brief-skill-evidence"]


def test_skill_edit_without_the_field_is_refused():
    hits = _hits([_impl(SKILL_A)])
    assert hits, "a brief editing " + SKILL_A + " with no observed_failure_evidence was accepted"
    assert hits[0][2] == SKILL_A, "the finding names " + repr(hits[0][2]) + ", not " + repr(SKILL_A)
    assert "observed_failure_evidence" in hits[0][3], (
        "the evidence does not name the missing field: " + repr(hits[0][3]))


def test_skill_edit_with_the_field_is_accepted():
    assert _hits([_impl(SKILL_A, observed_failure_evidence=GOOD_EVIDENCE)]) == [], (
        "a skill edit carrying the field was refused: the gate over-fires and will be routed around")


def test_blank_evidence_is_refused():
    assert _hits([_impl(SKILL_A, observed_failure_evidence="   ")]), (
        "whitespace-only observed_failure_evidence satisfied the gate")


def test_non_string_evidence_is_refused():
    assert _hits([_impl(SKILL_A, observed_failure_evidence=123)]), (
        "a non-string observed_failure_evidence satisfied the gate")


def test_every_skill_file_is_gated_not_one_hardcoded_path():
    assert _hits([_impl(SKILL_B)]), "a brief editing " + SKILL_B + " with no field was accepted"


def test_non_skill_targets_are_not_gated():
    assert _hits([_impl(NOT_A_SKILL)]) == [], "the gate fires on a file that is not a skill"
    assert _hits([_impl(NOT_SKILL_MD)]) == [], "the gate fires on a file that is not a SKILL.md"


def test_the_class_is_registered_for_the_ledger():
    assert "brief-skill-evidence" in scope_lint.CHECK_NAMES, (
        "a class absent from CHECK_NAMES is never written to scope_quality by record()")


def test_prose_about_a_failure_does_not_satisfy_the_field():
    """RCA rung 1: the guard is a FIELD on the dispatch, not a sentence inside it.

    A chunk whose prose names the observed failure and has no field is the exact shape of this
    finding: "I'll cite the dispatch that caused each edit", stated in prose, never enforced.
    """
    prose_only = _impl(SKILL_A, single_change="the observed failure: three skill edits shipped unguarded")
    assert _hits([prose_only]), "the gate accepted prose that describes a failure with no field"


if __name__ == "__main__":
    tests = sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))
    failed = []
    for name, fn in tests:
        try:
            fn()
            print("ok   " + name)
        except AssertionError as exc:
            failed.append(name)
            print("FAIL " + name + ": " + str(exc))
        except Exception as exc:  # a crash is a finding: print it, never swallow it
            failed.append(name)
            print("ERROR %s: %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d passed" % (len(tests) - len(failed), len(tests)))
    sys.exit(1 if failed else 0)
