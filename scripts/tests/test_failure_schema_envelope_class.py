#!/usr/bin/env python3
"""t1375: workspace/failure-schema.json must carry a `tool-envelope-invalid-args` cause class.

RCA workspace/rca/795797bc0db8.md (dsh session-8655bd20): fourteen byte-identical
`invalid arguments: missing required property "description"` rejections, 2m48s apart from
00:42:13Z to 00:45:01Z, byte-identical for `python3 scripts/taskstore.py --help` and for
`cat scripts/taskstore.py`, at 11 ms latency -- so the command was never executed
(rca/795797bc0db8.md:20,37,45). The lane read that envelope rejection as a taskstore flag and
wrote the mis-diagnosis into workspace/deferrals.jsonl:556.

The durable record a future reader consults is workspace/failure-schema.json. The class only
exists if the file has it, and it only routes if the `cause_category` it is filed under is a key
of `_cause_categories` in the same file -- whose `_routing_rule` says the category decides what
happens next. Filed `content`, the routing is 'note, front, re-scope the brief': every future
reader is sent upstream to fix a command that never ran. This suite pins that record, read the
way tooling reads it: json.loads on the real file. No model call, no subprocess, no live state,
nothing written.
"""
import copy
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "workspace" / "failure-schema.json"
CLASS = "tool-envelope-invalid-args"


def schema():
    """Read the real, standing schema file. json.loads is the consumer's read."""
    return json.loads(SCHEMA.read_text(encoding="utf-8"))


def envelope_rows(data):
    return [f for f in data["failures"] if f.get("class") == CLASS]


def the_row(data):
    rows = envelope_rows(data)
    assert len(rows) == 1, (
        "workspace/failure-schema.json must carry exactly one `failures` entry whose \"class\" "
        "is %r; found %d. The file is append-only (`_grows_by`: 'Entries are never deleted') "
        "-- add a new entry, do not fold this class into an existing one."
        % (CLASS, len(rows)))
    return rows[0]


def known_categories(data):
    return {k for k in data["_cause_categories"] if not k.startswith("_")}


def test_class_is_recorded_exactly_once():
    the_row(schema())


def test_class_is_filed_as_a_harness_capability():
    row = the_row(schema())
    cat = row.get("cause_category")
    assert cat == "harness-capability", (
        "a tool validator rejecting its own call envelope is neither the model's fault nor the "
        "brief's; filing it under %r routes it to 'note, front, re-scope the brief' and sends "
        "every future reader upstream to fix a command that was never executed "
        "(workspace/rca/795797bc0db8.md section 3). The category must be 'harness-capability'."
        % (cat,))


def test_category_is_one_the_router_actually_knows():
    data = schema()
    row = the_row(data)
    assert row.get("cause_category") in known_categories(data), (
        "cause_category %r is not a key of `_cause_categories` in the same file, so no consumer "
        "can route on it -- a class named only in prose is a class that is never applied."
        % (row.get("cause_category"),))


def test_record_names_its_subject_and_its_remedy():
    row = the_row(schema())
    text = " ".join(str(row.get(k, "")) for k in ("reason", "evidence", "defense")).lower()
    assert "envelope" in text, (
        "the record must name its subject -- the tool CALL envelope, not the command. A future "
        "reader searching for why an INVALID_ARGS is not a command fault finds nothing.")
    assert "description" in text, "the record must name the property the validator named"
    assert "never executed" in text, (
        "the record must carry the fact that makes the class self-identifying: the command was "
        "never executed (the message prescribed in workspace/rca/795797bc0db8.md:78).")
    assert "no defense written yet" not in text, (
        "this class exists to BE the defense; it must not be recorded in the shape "
        "'no defense written yet' that the entries it replaces carried.")


def test_a_falsified_entry_is_caught():
    """Non-vacuity: a row named right and filed wrong must NOT satisfy the routing check."""
    base = copy.deepcopy(schema())
    base["failures"] = [f for f in base["failures"] if f.get("class") != CLASS]
    assert envelope_rows(base) == [], "the lookup is not scoped to the class name"
    row = copy.deepcopy(the_row(schema()))
    row["cause_category"] = "content"
    mutated = copy.deepcopy(base)
    mutated["failures"] = list(base["failures"]) + [row]
    assert the_row(mutated).get("cause_category") != "harness-capability", (
        "routing assertion is vacuous: a row filed as `content` satisfies it")


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print("ok   %s" % name)
        except AssertionError as exc:
            failed.append(name)
            print("FAIL %s -- %s" % (name, exc))
    print("%d passed, %d failed, %d total" % (len(tests) - len(failed), len(failed), len(tests)))
    raise SystemExit(1 if failed else 0)
