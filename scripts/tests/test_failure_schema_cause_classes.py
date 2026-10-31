#!/usr/bin/env python3
"""t765 oracle: a cause class that names a control must name the artefact that binds it.

RED ON MAIN for the missing change: scripts/failure_schema.py defines neither CLASS_REQUIRED
nor validate() -- the numbered read of that file (88 lines) shows module-level names RULES
(29-38), classify (41-51), append (54-75), record_failure (78-84) and nothing else. The import
therefore succeeds and every attribute lookup below raises AttributeError.

Why the class is needed: workspace/failure-schema.json lines 4-11 list the cause categories --
content, external, harness-capability, environment-precondition -- and the failures list from
line 34 records class/cause_category/instances/reason/evidence/added_by with no required-field
gate. Nothing in the file, and nothing in scripts/failure_schema.py's RULES, can state 'a
written control that does not bind at the point its own text names' -- which is why
workspace/rca/23a856ff7eed.md recorded L-117's recurrence as an unresolved finding with
covered_by ''.

Read-only on the live schema: the only writes go to a tempfile copy.
"""
import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import failure_schema

SCHEMA = ROOT / "workspace" / "failure-schema.json"

# L-117's shape: a control whose ENFORCEMENT line declines to add a check. Two fixtures with
# DISTINCT endpoints and DISTINCT artefacts, so a validator that accepts any one non-empty
# string cannot satisfy this suite by accepting one string.
L117 = {
    "class": "control-not-enforced",
    "cause_category": "content",
    "application_endpoint": "writing a test that verifies a function used by production code",
    "binding_check": "scripts/tests/test_failure_schema_contract.py",
    "reason": "the test drove the helper while the mutation killed the call site",
}
MUTATION = {
    "class": "control-not-enforced",
    "cause_category": "content",
    "application_endpoint": "declaring the entry point a mutation suite drives",
    "binding_check": "scripts/failure_schema.py:54",
    "reason": "the registry records the suite, not the entry point the suite must drive",
}


def test_the_class_declares_the_fields_that_make_it_stateable():
    assert failure_schema.CLASS_REQUIRED["control-not-enforced"] == ("application_endpoint", "binding_check")


def test_a_row_naming_resolvable_artefacts_is_accepted():
    for row in (L117, MUTATION):
        assert failure_schema.validate(row) == "", (row["binding_check"], failure_schema.validate(row))


def test_a_row_with_no_binding_check_is_refused_naming_the_key():
    row = {k: v for k, v in L117.items() if k != "binding_check"}
    v = failure_schema.validate(row)
    assert v and "binding_check" in v.lower(), v


def test_a_blank_binding_check_is_refused_naming_the_key():
    for bad in ("", "   ", None):
        v = failure_schema.validate(dict(L117, binding_check=bad))
        assert v and "binding_check" in v.lower(), (bad, v)


def test_a_prose_binding_check_is_refused_naming_the_key():
    # L-117's own wording: a control that declares its own gap and binds nothing to it.
    v = failure_schema.validate(dict(L117, binding_check="no new check of its own"))
    assert v and "binding_check" in v.lower(), v


def test_a_binding_check_naming_a_file_that_does_not_exist_is_refused():
    v = failure_schema.validate(dict(L117, binding_check="scripts/tests/test_not_in_the_repo.py"))
    assert v and "binding_check" in v.lower(), v


def test_a_binding_check_line_past_the_end_of_its_file_is_refused():
    v = failure_schema.validate(dict(L117, binding_check="scripts/failure_schema.py:99999"))
    assert v and "binding_check" in v.lower(), v


def test_a_missing_application_endpoint_is_refused_naming_the_key():
    row = {k: v for k, v in L117.items() if k != "application_endpoint"}
    v = failure_schema.validate(row)
    assert v and "application_endpoint" in v.lower(), v


def test_an_existing_class_still_needs_no_binding():
    # The appender writes rows for classes that predate this change. Requiring a binding_check
    # from them would make every future mechanical append a failure.
    row = {"class": "window-full", "cause_category": "content", "reason": "the file was pasted whole"}
    assert failure_schema.validate(row) == "", failure_schema.validate(row)


def test_appending_an_unbound_control_row_writes_nothing():
    # The behaviour, not the message: a validator that is computed and never called by append()
    # passes a shape-only oracle and fails this one.
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "failure-schema.json"
        p.write_text(json.dumps({"failures": []}), encoding="utf-8")
        fields = {k: v for k, v in L117.items() if k not in ("class", "cause_category", "reason")}
        refused = ""
        try:
            failure_schema.append("control-not-enforced", "content", "t765-fixture",
                                  "2026-10-08T00:00:00Z", "fixture reason", "fixture evidence",
                                  path=p, extra=fields)
        except ValueError as exc:
            refused = str(exc)
        assert refused and "binding_check" in refused.lower(), refused
        assert json.loads(p.read_text(encoding="utf-8"))["failures"] == [], "refused row was written anyway"


def test_appending_a_bound_control_row_lands_with_both_fields():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "failure-schema.json"
        p.write_text(json.dumps({"failures": []}), encoding="utf-8")
        out = failure_schema.append("control-not-enforced", "content", "t765-fixture",
                                    "2026-10-08T00:00:00Z", L117["reason"], "fixture evidence",
                                    path=p,
                                    extra={"application_endpoint": L117["application_endpoint"],
                                           "binding_check": L117["binding_check"]})
        rows = json.loads(p.read_text(encoding="utf-8"))["failures"]
        assert out == "new-class", out
        assert len(rows) == 1, rows
        assert rows[0]["class"] == "control-not-enforced", rows
        assert rows[0]["binding_check"] == L117["binding_check"], rows
        assert rows[0]["application_endpoint"] == L117["application_endpoint"], rows


def test_an_existing_class_appends_exactly_as_it_does_today():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "failure-schema.json"
        p.write_text(json.dumps({"failures": []}), encoding="utf-8")
        out = failure_schema.append("window-full", "content", "p999-t765", "2026-10-08T00:00:00Z",
                                    "the brief pasted the whole file", "attempt 1 WINDOW FULL",
                                    path=p)
        assert out == "new-class", out
        rows = json.loads(p.read_text(encoding="utf-8"))["failures"]
        assert rows[0]["class"] == "window-full" and len(rows) == 1, rows
        # idempotent on (at, chunk) -- failure_schema.py:67-68 -- unchanged by this change
        assert failure_schema.append("window-full", "content", "p999-t765", "2026-10-08T00:00:00Z",
                                     "same sighting", "again", path=p) == "duplicate"
        assert len(json.loads(p.read_text(encoding="utf-8"))["failures"]) == 1


def test_the_real_schema_is_self_consistent_once_the_row_lands():
    # Ratchet, honest about being VACUOUS TODAY: no control-not-enforced row exists on main
    # (grep of *.py, *.json, *.md found the string only in workspace/tasks/ and workspace/rca/).
    # It bites when the data half lands the row: the schema must not ship a class it cannot state.
    data = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for row in data.get("failures", []):
        if row.get("class") == "control-not-enforced":
            assert failure_schema.validate(row) == "", row


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
