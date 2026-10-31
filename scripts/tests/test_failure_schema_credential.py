#!/usr/bin/env python3
"""t1332 -- credential-exposure is a category the schema enforces ON WRITE.

RCA 392d0498c583 (session ef02843a, turn 309 of 358): a rotate+monitor duty was discharged as a
bare "say the word and I'll rotate both" verbal handoff -- no number, no owner, no trigger --
and it evaporated at session end. The reviewer's `covered_by: ""` confirms no task carried it.
The missing category is the root cause (RCA section 5, Rung 1).

The contract being pinned: a credential that entered a durable artifact MUST end in
(a) a rotation command executed AND its output recorded, or
(b) a FILED task with a task number, a named owner and a monitor trigger.
A bare deferral is an invalid state -- not a soft warning.

Why these assertions live in a test and not in a brief: a rule stated only in prose is one the
next dispatch may legally ignore (docs/skills/writing-oracles-for-delegation). The enforcement
point is failure_schema.append() -- the only writer the schema has -- so a category that is not
validated there is a category that accepts anything.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import failure_schema  # noqa: E402

CLASS = "credential-exposure"

# Two DISTINCT real artefacts, not one repeated placeholder string: a rotation record and a
# filed task are different shapes and the suite must be able to tell them apart (t471/t297/t370,
# RCA t613 -- 'it uses one repeated placeholder string where the real code has two distinct
# values').
ROTATED = {
    "exposure_surface": "transcript",
    "disposition": "rotated",
    "rotation_command": "python3 scripts/workspace_mcp/server.py --rotate-credentials",
    "output_recorded": "workspace/run-logs/rotate-2026-10-17.log",
}
FILED = {
    "exposure_surface": "tracked-file",
    "disposition": "filed",
    "task_id": "t1332",
    "owner": "jas",
    "monitor_trigger": "rotate if mcp-calls.jsonl shows >=1 request from a new IP since exposure",
}


def _schema(tmp):
    """A schema file inside the caller's tempdir. Never the live workspace/failure-schema.json."""
    p = Path(tmp) / "failure-schema.json"
    p.write_text(json.dumps({"failures": []}), encoding="utf-8")
    return p


def _append(p, fields, chunk):
    return failure_schema.append(CLASS, "content", chunk, "2026-10-17T01:00:00Z",
                                "reason", "evidence", path=p, fields=fields)


def test_the_category_name_is_public():
    """Consumers (RCA checklist item 2's scanner) must be able to name the category without
    hardcoding a string -- the pattern the file already sets with CHANGE_LEFT_IN_TREE."""
    got = getattr(failure_schema, "CREDENTIAL_EXPOSURE", "<<ABSENT>>")
    assert got == CLASS, f"expected CREDENTIAL_EXPOSURE == {CLASS!r}, got {got!r}"


def test_a_bare_deferral_is_rejected_on_append():
    """THE defect this task exists for. 'say the word and I'll rotate' carries no number, no
    owner, no trigger. It must not be appendable as a valid row."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _schema(tmp)
        for bare in (
            {},
            {"disposition": "filed"},
            {"disposition": "filed", "task_id": "t1332"},
            {"disposition": "rotated"},
        ):
            try:
                _append(p, dict(bare), "bare")
            except ValueError:
                continue
            raise AssertionError(
                f"append() accepted credential-exposure fields {bare!r} with no disposition; "
                "a bare deferral is the invalid state RCA 392d0498c583 is about")
        rows = json.loads(p.read_text(encoding="utf-8"))["failures"]
        assert rows == [], (
            f"a REJECTED row must not be recorded, got {rows!r}: a row written as a side effect "
            "of a rejected append is a record of something that did not happen")


def test_a_rotation_disposition_needs_both_its_halves():
    """RCA Rung 1 branch (a): a rotation command executed AND its output recorded. A row naming
    the command without the output's location records an intention, not an act -- and the
    transcript finding is exactly a promise of a command that ran with no record kept."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _schema(tmp)
        for drop in ("rotation_command", "output_recorded"):
            fields = dict(ROTATED)
            fields.pop(drop)
            try:
                _append(p, fields, f"rot-{drop}")
                raise AssertionError(f"disposition 'rotated' accepted with {drop} missing")
            except ValueError:
                pass


def test_a_filed_disposition_needs_number_owner_and_trigger():
    """RCA Rung 1 branch (b): a FILED task with number + named owner + monitor trigger. Dropping
    any one of the three must be refused -- they are the three things the 2026-08-19 handoff had
    none of."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _schema(tmp)
        for drop in ("task_id", "owner", "monitor_trigger"):
            fields = dict(FILED)
            fields.pop(drop)
            try:
                _append(p, fields, f"filed-{drop}")
                raise AssertionError(f"disposition 'filed' accepted with {drop} missing")
            except ValueError:
                pass


def test_an_exposure_surface_is_named():
    """'a credential entered a durable artifact' is the trigger. A row that never says WHERE is
    unfalsifiable, so the scanner's own finding could not be reproduced from the record."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _schema(tmp)
        for drop in ("exposure_surface",):
            fields = dict(FILED)
            fields.pop(drop)
            try:
                _append(p, fields, "filed-no-surface")
                raise AssertionError("row accepted with exposure_surface missing")
            except ValueError:
                pass


def test_a_task_id_must_be_a_real_number_not_a_word():
    """`task_id: "next session"` passes a check that only asks for a non-empty string -- the
    vacuous-oracle shape. A tNNN is the unit this repository files work in."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _schema(tmp)
        for bad in ("next session", "soon", "t", "t12345678", "T1332", "4321t"):
            fields = dict(FILED)
            fields["task_id"] = bad
            try:
                _append(p, fields, f"badid-{bad}")
                raise AssertionError(f"task_id {bad!r} accepted; it is not a tNNN id")
            except ValueError:
                pass


def test_an_unrecognised_disposition_is_rejected():
    """Inverted check (docs/skills/planning-for-sustainment): enumerate the APPROVED set and
    treat everything outside it as a finding. 'unresolved' and 'deferred' are the words the
    finding file has been saying for days while nothing acted."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _schema(tmp)
        for bad in ("unresolved", "unresolved (fill later)", "deferred", "", "   ", None):
            fields = dict(FILED)
            fields["disposition"] = bad
            try:
                _append(p, fields, f"disp-{bad}")
                raise AssertionError(f"disposition {bad!r} accepted; not in the approved set")
            except ValueError:
                pass


def test_the_two_valid_dispositions_are_accepted_and_landed_whole():
    """The positive half, and the downstream half: a real rotation record and a real filed task
    both land, ONE row, two instances, carrying the field VALUES -- not merely a truthy dict.
    Asserting the values is what tells 'the field was read' from 'the field was used'
    (t471/t297/t370, RCA t613)."""
    with tempfile.TemporaryDirectory() as tmp:
        p = _schema(tmp)
        assert _append(p, dict(ROTATED), "c-rot") == "new-class"
        assert _append(p, dict(FILED), "c-filed") == "appended"
        rows = json.loads(p.read_text(encoding="utf-8"))["failures"]
        assert len(rows) == 1, f"one class must be one row, got {len(rows)}: {rows!r}"
        assert rows[0]["class"] == CLASS, rows[0]["class"]
        instances = rows[0]["instances"]
        assert len(instances) == 2, instances
        rot, filed = instances[0], instances[1]
        assert rot["disposition"] == "rotated", rot
        assert rot["exposure_surface"] == "transcript", rot
        assert rot["rotation_command"] == ROTATED["rotation_command"], rot
        assert rot["output_recorded"] == ROTATED["output_recorded"], rot
        assert filed["disposition"] == "filed", filed
        assert filed["task_id"] == "t1332", filed
        assert filed["owner"] == "jas", filed
        assert filed["monitor_trigger"] == FILED["monitor_trigger"], filed


def test_validation_names_the_field_that_was_missing():
    """ValueError text must say WHICH field -- the reason travels to the task's notes as the
    builder's failure reason, and an anonymous ValueError is undiagnosable
    (docs/skills/never-truncate-a-record: a record that says only 'invalid' is a record nobody
    can act on)."""
    for drop in ("rotation_command", "output_recorded", "task_id", "owner",
                 "monitor_trigger", "exposure_surface"):
        fields = dict(ROTATED if drop in ("rotation_command", "output_recorded") else FILED)
        fields.pop(drop)
        try:
            failure_schema.validate_landing(CLASS, fields)
            raise AssertionError(f"validate_landing accepted a credential-exposure row "
                                f"with {drop} missing")
        except ValueError as exc:
            assert drop in str(exc), (
                f"the reason must name the missing field {drop!r}, got: {exc}")


def test_the_new_category_does_not_redefine_the_existing_one():
    """Negative assertion: adding credential-exposure must not change what validate_landing does
    to change-left-in-tree -- the class already on the books. This scope is an addition, not a
    redefinition of the schema."""
    ok = {"path": "scripts/x.py", "head": "abc1234", "actor": "Q16",
          "disposition": "landed", "lands_via": "t1332"}
    assert failure_schema.validate_landing("change-left-in-tree", ok) is None, \
        "a complete change-left-in-tree row must still validate"
    for drop in ("path", "head", "actor", "disposition", "lands_via"):
        fields = dict(ok)
        fields.pop(drop)
        try:
            failure_schema.validate_landing("change-left-in-tree", fields)
            raise AssertionError(f"change-left-in-tree accepted with {drop} missing")
        except ValueError:
            pass
    fields = dict(ok)
    fields["lands_via"] = "next sprint"
    try:
        failure_schema.validate_landing("change-left-in-tree", fields)
        raise AssertionError("change-left-in-tree accepted lands_via 'next sprint'")
    except ValueError:
        pass


if __name__ == "__main__":
    # pytest is not installed in this repository: the builder runs `python3 <this file>`, and a
    # suite whose tests are defined but never run proves nothing (10 of 75 oracles, 2026-09-28
    # calibration). A crash counts as a FAIL, never as a skip.
    _tests = [(k, v) for k, v in sorted(globals().items())
              if k.startswith("test_") and callable(v)]
    _failed = []
    for _name, _fn in _tests:
        try:
            _fn()
            print(f"PASS {_name}")
        except Exception as _exc:  # noqa: BLE001 -- a crash is a failing case, not a skip
            print(f"FAIL {_name}: {type(_exc).__name__}: {_exc}")
            _failed.append(_name)
    print(f"{len(_tests) - len(_failed)}/{len(_tests)} passed")
    raise SystemExit(1 if _failed else 0)
