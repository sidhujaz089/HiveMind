#!/usr/bin/env python3
"""t1315 oracle: the capability-requirement rule is codified as a failure class.

Rule (workspace/tasks/t1315--...md, from RCA workspace/rca/15e14f4b35d0.md):
a requirement worded "a peer / one instance must not be able to <privileged write>"
is a capability/access-control requirement and is NOT shippable as prose. Shipping it
requires BOTH (1) a named denylist entry in a jurisdiction/permission schema and
(2) a two-direction refusal test; and retiring the subsystem that carried the
requirement must record a decision closing every requirement it protected.

Durable home: workspace/failure-schema.json -- its `_what` says an entry here is one
every scope and brief must address, and scripts/manager_prompt.py renders each failure
row's `defense` into every Manager scope, so the class fires when a capability/permission
requirement is scoped, not as prose read after the fact.

RED ON MAIN: no row has this class id, so the 'exactly one row' assertion sees 0 matches.
Read-only: reads one JSON file; no subprocess/model/network; writes nothing.
"""
import json
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "workspace" / "failure-schema.json"

# Exact id -- the decay test: a guard that asserts "any row mentions peer" goes green
# while the rule is renamed away; asserting the exact id keeps it bound.
CLASS_ID = "capability-requirement-unenforced"

# The file's own taxonomy (cause_category values present on main).
KNOWN_CATEGORIES = {"content", "external", "harness-capability", "environment-precondition", "unclassified"}


def failures():
    data = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "failure-schema.json must be a JSON object"
    assert isinstance(data.get("failures"), list), "failure-schema.json must have a `failures` list"
    return data["failures"]


def the_row():
    matches = [r for r in failures() if isinstance(r, dict) and r.get("class") == CLASS_ID]
    assert len(matches) == 1, f"expected exactly one {CLASS_ID!r} row, found {len(matches)}"
    return matches[0]


def defense_text():
    d = the_row().get("defense")
    assert isinstance(d, str) and d.strip(), "the row must carry a non-empty `defense` (the text the manager prompt renders)"
    return d.lower()


def test_exactly_one_row_carries_the_rule_id():
    assert len([r for r in failures() if isinstance(r, dict) and r.get("class") == CLASS_ID]) == 1


def test_cause_category_is_one_the_file_already_uses():
    assert the_row().get("cause_category") in KNOWN_CATEGORIES, the_row().get("cause_category")


def test_reason_is_present():
    reason = the_row().get("reason")
    assert isinstance(reason, str) and reason.strip(), "row needs a non-empty reason"


def test_defense_requires_a_named_denylist_in_a_jurisdiction_or_permission_schema():
    d = defense_text()
    assert "denylist" in d, "the defense must name the denylist entry"
    assert ("jurisdiction" in d) or ("permission" in d), "the defense must name the jurisdiction/permission schema"


def test_defense_requires_a_two_direction_refusal_test():
    d = defense_text()
    assert "refusal" in d, "the defense must require a refusal test"
    assert ("two-direction" in d) or ("both direction" in d), "the defense must require BOTH directions, not one (one direction is decoration)"


def test_defense_requires_a_recorded_decision_on_retirement():
    d = defense_text()
    assert "record" in d, "the defense must require a recorded decision"
    assert ("decision" in d) or ("close" in d), "the retirement half must close the requirements the subsystem protected"


def _main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failed.append(name)
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"{len(tests) - len(failed)}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
