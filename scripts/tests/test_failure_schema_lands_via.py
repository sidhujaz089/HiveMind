#!/usr/bin/env python3
"""test_failure_schema_lands_via.py -- t770.

A `change-left-in-tree` defect names a tracked file left dirty; the record is worthless
unless it names the number that lands the fix (RCA 4f5179883631 D2: "No tNNN" -- the
finding had covered_by "" and no next actor).

RED ON MAIN for the stated reason: scripts/failure_schema.py has no CHANGE_LEFT_IN_TREE,
no REQUIRED_FIELDS, no LANDS_VIA and no `fields=` on append() -- so every test below
raises AttributeError/TypeError naming a name that does not exist yet. Nothing here fails
for a reason of its own: the module imports, and record_failure() is asserted on main.

Run as `python3 scripts/tests/test_failure_schema_lands_via.py` (pytest is not installed).
Uses tempfile only; touches no live state, no model, no server, no subprocess.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import failure_schema  # noqa: E402

# Real values from the finding, not placeholders: the dirty path and head are the RCA's,
# "299f9761" is the non-task token the cover list accepted (rca/4f5179883631.md:51).
DIRTY = "projects/local-inference/models/ROLES.json"
GOOD = {
    "path": DIRTY,
    "head": "b236a737",
    "actor": "claude-code",
    "disposition": "unresolved",
    "lands_via": "t770",
}


def _schema(tmp):
    """A schema file in the shape every existing append() caller expects."""
    p = Path(tmp) / "failure-schema.json"
    p.write_text(json.dumps({"failures": []}, ensure_ascii=False), encoding="utf-8")
    return p


def _rows(p):
    return json.loads(p.read_text(encoding="utf-8"))["failures"]


def test_class_name_and_required_fields_are_declared():
    assert failure_schema.CHANGE_LEFT_IN_TREE == "change-left-in-tree", \
        "class name is the one the schema JSON entry will use"
    assert set(failure_schema.REQUIRED_FIELDS) == {
        "path", "head", "actor", "disposition", "lands_via"
    }, f"required fields are the RCA's five, not a subset: {failure_schema.REQUIRED_FIELDS}"


def test_complete_defect_is_written_with_its_landing_number():
    with tempfile.TemporaryDirectory() as tmp:
        p = _schema(tmp)
        out = failure_schema.append(
            "change-left-in-tree", "content", "p301-t770", "2026-10-08T00:00:00Z",
            "ROLES.json dirty at session close", "git status --porcelain: M " + DIRTY,
            path=p, fields=dict(GOOD))
        assert out == "new-class", f"first sighting of a new class: {out}"
        rows = _rows(p)
        assert len(rows) == 1, f"exactly one row appended: {rows}"
        assert rows[0]["class"] == "change-left-in-tree", rows[0]["class"]
        # The five fields must land IN THE ROW, with the real values, not merely be accepted.
        assert rows[0]["instances"] == [{
            "at": "2026-10-08T00:00:00Z", "chunk": "p301-t770",
            "evidence": "git status --porcelain: M " + DIRTY,
            "path": DIRTY, "head": "b236a737", "actor": "claude-code",
            "disposition": "unresolved", "lands_via": "t770",
        }], rows[0]["instances"]


def test_a_dirty_path_without_a_landing_number_is_refused_and_writes_nothing():
    with tempfile.TemporaryDirectory() as tmp:
        p = _schema(tmp)
        before = p.read_text(encoding="utf-8")
        fields = dict(GOOD)
        del fields["lands_via"]
        try:
            failure_schema.append("change-left-in-tree", "content", "p301-t770",
                                  "2026-10-08T00:00:00Z", "why", "evidence",
                                  path=p, fields=fields)
        except ValueError as exc:
            assert "lands_via" in str(exc), \
                f"the refusal must name the field that is missing: {exc}"
        else:
            raise AssertionError("a change-left-in-tree row with no lands_via was accepted")
        assert p.read_text(encoding="utf-8") == before, "a refusal must not write the row"
        assert _rows(p) == [], f"the file still holds no defect row: {_rows(p)}"


def test_lands_via_must_be_a_task_number_not_any_string():
    for bad in ("299f9761", "", "   ", "770", "T770", "t770x", None, 770):
        with tempfile.TemporaryDirectory() as tmp:
            p = _schema(tmp)
            before = p.read_text(encoding="utf-8")
            fields = dict(GOOD, lands_via=bad)
            try:
                failure_schema.append("change-left-in-tree", "content", "c", "at", "why",
                                      "evidence", path=p, fields=fields)
            except ValueError as exc:
                assert "lands_via" in str(exc), f"{bad!r}: refusal names the field: {exc}"
            else:
                raise AssertionError(f"lands_via={bad!r} is not a tNNN and was accepted")
            assert p.read_text(encoding="utf-8") == before, f"{bad!r} was written to the schema"


def test_a_missing_required_field_is_named_by_name():
    for missing in ("path", "head", "actor", "disposition"):
        with tempfile.TemporaryDirectory() as tmp:
            p = _schema(tmp)
            fields = dict(GOOD)
            del fields[missing]
            try:
                failure_schema.append("change-left-in-tree", "content", "c", "at", "why",
                                      "evidence", path=p, fields=fields)
            except ValueError as exc:
                assert missing in str(exc), f"refusal names {missing!r}, not a generic error: {exc}"
            else:
                raise AssertionError(f"change-left-in-tree accepted with no {missing!r}")
            assert _rows(p) == [], f"{missing!r} missing but a row was written: {_rows(p)}"


def test_other_classes_are_not_required_to_carry_a_number():
    """The gate is scoped to the new class; it must not block the writer's existing callers."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _schema(tmp)
        out = failure_schema.append("tests-failed", "content", "p1-x", "2026-10-08Z",
                                    "why", "evidence", path=p, fields={"path": "scripts/x.py"})
        assert out == "new-class", out
        rows = _rows(p)
        assert len(rows) == 1 and rows[0]["class"] == "tests-failed", rows
        assert rows[0]["instances"][0]["path"] == "scripts/x.py", rows[0]["instances"]
        assert "lands_via" not in rows[0]["instances"][0], \
            f"a tests-failed row grew a lands_via: {rows[0]['instances'][0]}"


def test_record_failure_still_writes_the_row_it_writes_today():
    """Behaviour, not shape: the harness's own entry point is unchanged by this brief."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _schema(tmp)
        cls, out = failure_schema.record_failure(
            "p301-t770", "2026-10-08T00:00:00Z",
            "attempt 1 ... tests fail (1: test_x.py, 607s)", path=p)
        assert cls == "tests-failed", cls
        assert out == "new-class", out
        rows = _rows(p)
        assert len(rows) == 1, rows
        assert rows[0]["class"] == "tests-failed" and rows[0]["cause_category"] == "content", rows[0]
        inst = rows[0]["instances"][0]
        assert inst == {"at": "2026-10-08T00:00:00Z", "chunk": "p301-t770",
                        "evidence": "attempt 1 ... tests fail (1: test_x.py, 607s)"}, inst


if __name__ == "__main__":
    names = sorted(n for n, f in globals().items() if n.startswith("test_") and callable(f))
    failed = 0
    for name in names:
        try:
            globals()[name]()
        except Exception as exc:  # noqa: BLE001 -- RECORDS the reason and fails the run
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"ok   {name}")
    print(f"{len(names) - failed}/{len(names)} passed")
    sys.exit(1 if failed else 0)
