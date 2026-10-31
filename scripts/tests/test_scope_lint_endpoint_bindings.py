#!/usr/bin/env python3
"""Oracle for t1015 items (b)/(c): scope_lint must refuse a manager chunk that names a
model-serving endpoint (host:port) in the builder-facing prose without naming the ONE approved
resolver. Allowlist inversion per docs/skills/planning-for-sustainment: the approved set is the
one resolver, and every reference outside it is a finding.

Run: python3 scripts/tests/test_scope_lint_endpoint_bindings.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import scope_lint

CLASS = "endpoint-hardcoded"
ENDPOINT = "http://127.0.0.1:4000/v1/chat/completions"
RESOLVER = "scripts/probe_batch.py"


def _findings(single_change):
    """Findings of CLASS for one implementation chunk, read through the public entry point
    scope_lint.static() -- the one lint() and --no-run both reach."""
    chunk = {
        "kind": "implementation",
        "file": "scripts/probe.py",
        "target": "scripts/tests/test_scope_lint.py",
        "done_when": "python3 scripts/tests/test_scope_lint.py exits 0",
        "single_change": single_change,
        "what_is_wrong": "",
    }
    return [f for f in scope_lint.static([chunk], ROOT) if f[0] == CLASS]


def test_endpoint_named_without_the_resolver_is_flagged():
    found = _findings("Point the probe at " + ENDPOINT + " and record the verdict.")
    assert len(found) == 1, (
        "a chunk naming a model-serving endpoint must produce exactly one "
        + CLASS + " finding, got " + repr(found))


def test_the_finding_says_what_to_do():
    found = _findings("Point the probe at " + ENDPOINT + " and record the verdict.")
    assert found, "no finding to check the wording of"
    evidence = found[0][3]
    assert RESOLVER in evidence, (
        "the evidence must name the approved resolver so the builder can act on it: " + repr(evidence))


def test_naming_the_approved_resolver_clears_it():
    found = _findings("Point the probe at " + ENDPOINT + " through " + RESOLVER + ", which owns it.")
    assert found == [], (
        "naming the approved resolver must clear the finding -- this is an allowlist, "
        "not a ban on writing endpoints: " + repr(found))


def test_prose_about_resolving_is_not_the_resolver():
    found = _findings("Use the gateway endpoint " + ENDPOINT + ", resolved at up time.")
    assert len(found) == 1, (
        "prose that merely says the endpoint is resolved is not the approved resolver -- "
        "that is the prose rule this class exists to replace: " + repr(found))


def test_prose_naming_no_endpoint_is_untouched():
    found = _findings("Add a 10 second bound to the loopback probe and record the row.")
    assert found == [], (
        "ordinary brief prose must not fire -- a check that fires on everything gets "
        "routed around with --no-verify: " + repr(found))


def test_the_class_is_recorded():
    assert CLASS in scope_lint.CHECK_NAMES, (
        CLASS + " is checked but not recorded, so a scope that never trips it leaves no "
        "passing record: " + repr(tuple(scope_lint.CHECK_NAMES)))


if __name__ == "__main__":
    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("ok " + name)
        except AssertionError as exc:
            failed += 1
            print("FAIL " + name + ": " + str(exc))
    print(str(len(tests) - failed) + "/" + str(len(tests)) + " passed")
    sys.exit(1 if failed else 0)
