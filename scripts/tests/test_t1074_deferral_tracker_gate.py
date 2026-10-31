#!/usr/bin/env python3
"""t1074: a session-review finding disposed as a deferral must name the task that carries it.

Evidence: workspace/rca/92326eec9f88.md and
workspace/session-reviews/findings/92326eec9f88.json -- that record is kind="deferral",
disposition="deferred" with covered_by empty, and it sat in the queue for 30 days because
every surface in this repo keys on a task id.

scripts/session_review.py already ships the deferral contract: DEFERRAL_FIELDS,
DeferralContractError, deferral_violations (it raises TypeError on a non-mapping, returns []
for any kind that is not "deferral", and appends one "missing <field>" / "<field> is empty"
string per required field) and refuse_if_deficient (it raises DeferralContractError naming
every deficient field and returns None for a record that satisfies the contract). Two things
are missing, both inside scripts/session_review.py: the contract does not require the tracker
id (covered_by), and grep -n 'refuse_if_deficient' scripts/session_review.py finds only its
own definition -- _keep(), the finding writer, never consults it, so a deferral that names no
task id is written as a terminal finding. That is what this suite pins, at the write point.

WHY THIS IS A NEW FILE AND NOT AN EDIT (2026-10-15): scripts/tests/test_session_review_deferral_id.py
-- the suite committed at 8f9d5ac0 -- is unsatisfiable. Its
test_a_deferred_finding_with_no_task_id_is_not_written asserts refusals(logs) == [] while
the next test asserts len(refusals(logs)) == 1 for the SAME input, so no implementation can
green it; the preflight recorded that (workspace/preflight-objections.jsonl,
brief-p475-t1074-session-review, verdict NOT SATISFIABLE). scripts/bridge.py refuses to
overwrite an existing test file, so the corrected text lives here; that suite is retired by
Claude's commit, and its tests are carried over verbatim except for the one assertion named
below. The same defect sits in scripts/tests/test_t1048_deferral_write_gate.py, which is red
on main for the same reason (I ran it: 3/7 passed).

WHY AN ABSENT covered_by KEY IS NOT REFUSED: scripts/tests/test_deferral_contract.py (green
on main, 12/12) builds its complete record with kind, quote, tracked_as, owner,
verify_command, decay_test and intended_state -- no covered_by key -- and asserts
deferral_violations returns []. Refusing a record that omits the key turns that suite red for
no evidence gain: the record this task exists to stop carries the key with an empty value.
So the rule pinned here is: a deferral record that CARRIES covered_by must carry a non-blank
value. The last test below pins the negative case so a blanket refusal cannot pass.

Run: python3 scripts/tests/test_t1074_deferral_tracker_gate.py  (pytest is not installed).
No live state is touched: FINDINGS, _log and _now are redirected to a temp dir / fakes and
restored in a finally, and note_task is faked so the live task file is never written. No
subprocess, no urlopen, no model, nothing left running. Do not invent a function, a module
or a file path: everything referenced above is real in scripts/session_review.py.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr

# The quote of the finding this task exists to stop. Deliberately free of identifier-shaped
# tokens (6+ chars containing _ . / or a digit), so _archive_cover's `if not words: return ""`
# guard fires and the test never reads the live task store.
QUOTE = ("The decision ledger was not started, so the decision has no record "
         "anyone will read")
QUOTE2 = ("The collector version field was never written, so the run has no recorded "
          "provenance")
TID = "t1074"
REQ = {"id": "721de5a0", "harness": "claude", "_candidates": ["t1074"]}
REQ_FILE = Path("claude-721de5a0-a559-4e99-81f6-3f8ec745ad46--from-74.json")


def deferred_no_id(**over):
    """The shape that died: a deferral disposed as deferred that names no task id."""
    r = {"kind": "deferral",
         "disposition": "deferred",
         "covered_by": "",
         "quote": QUOTE,
         "why_missed": "never mentioned again; no covering task found"}
    r.update(over)
    return r


def deferral_with_id(**over):
    """A deferral carrying a real task id AND every t607 field, so it satisfies the whole contract."""
    r = {"kind": "deferral",
         "disposition": "deferred",
         "covered_by": TID,
         "tracked_as": TID,
         "owner": "Manager",
         "verify_command": "python3 scripts/tests/test_t1074_deferral_tracker_gate.py",
         "decay_test": "a kind=deferral finding reaches the queue with no covered_by",
         "intended_state": "no deferral is written as a terminal outcome without naming its tracker",
         "quote": QUOTE,
         "why_missed": "never mentioned again; no covering task found"}
    r.update(over)
    return r


def unresolved_row(**over):
    """A non-deferral: it has no task id by design and must keep being written."""
    r = {"kind": "unresolved",
         "disposition": "unresolved",
         "covered_by": "",
         "quote": QUOTE2,
         "why_missed": "the queue has no covering task"}
    r.update(over)
    return r


def run_keep(found):
    """Call _keep() with every live-state surface redirected. Returns (new, covered, logs, payloads)."""
    tmp = Path(tempfile.mkdtemp(prefix="t1074-"))
    logs = []
    saved = (sr.FINDINGS, sr._log, sr._now)
    sr.FINDINGS = tmp / "findings"
    sr._log = lambda rec: logs.append(rec)
    sr._now = lambda: "2026-10-12T120000Z"
    try:
        new, covered = sr._keep(REQ_FILE, REQ, [dict(r) for r in found],
                                note_task=lambda *a, **k: None)
    finally:
        sr.FINDINGS, sr._log, sr._now = saved
        files = sorted((tmp / "findings").glob("*.json"))
        payloads = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in files}
        shutil.rmtree(tmp, ignore_errors=True)
    return new, covered, logs, payloads


def refusals(logs):
    return [l for l in logs if l.get("event") == "deferral-refused"]


def finding_rows(logs):
    """Rows the writer logged as a written finding (the event _keep logs for a new finding)."""
    return [l for l in logs if l.get("event") == "finding"]


def test_deferral_with_empty_covered_by_is_a_violation():
    """THE DEFECT: today deferral_violations accepts a deferral that names no tracker."""
    v = sr.deferral_violations(deferred_no_id())
    assert isinstance(v, list), "deferral_violations must return a list, got %r" % (v,)
    assert all(isinstance(x, str) for x in v), "violations must be strings: %r" % (v,)
    assert any("covered_by" in x for x in v), (
        "a kind=deferral record with covered_by empty produced no violation naming "
        "covered_by, the field the record is missing: %r" % (v,))


def test_whitespace_covered_by_is_not_a_task_id():
    """A truthiness-only check lets '   ' through; whitespace is not an id anyone can key on."""
    v = sr.deferral_violations(deferred_no_id(covered_by="   "))
    assert any("covered_by" in x for x in v), (
        "covered_by of pure whitespace is not a task id and must be refused: %r" % (v,))


def test_a_deferral_that_omits_the_covered_by_key_is_not_refused_on_it():
    """The narrowing guard, so the fix cannot be a blanket refusal: a record that carries NO
    covered_by key is not refused on that field. scripts/tests/test_deferral_contract.py is
    green on main and its complete record has no such key; refusing it would break a passing
    suite for no evidence gain."""
    r = deferred_no_id()
    r.pop("covered_by")
    v = sr.deferral_violations(r)
    assert not any("covered_by" in x for x in v), (
        "a deferral record with no covered_by key at all must not be refused on covered_by: "
        "%r" % (v,))


def test_deferral_that_carries_a_task_id_is_not_refused_on_the_tracker_field():
    """Cost guard: refusing every deferral is cheaper than refusing the deficient one."""
    v = sr.deferral_violations(deferral_with_id())
    assert not any("covered_by" in x for x in v), (
        "a deferral that names task id %r must not be refused on covered_by, or the writer "
        "will start minting filler tasks to go quiet: %r" % (TID, v))


def test_non_deferral_is_never_refused_on_the_tracker_field():
    """Cost guard: an unresolved finding has no task id by design; blocking it would refuse
    the whole session-review lane."""
    v = sr.deferral_violations(unresolved_row())
    assert not any("covered_by" in x for x in v), (
        "a kind=unresolved finding must never be blocked on covered_by: %r" % (v,))


def test_refuse_if_deficient_raises_naming_the_tracker_field():
    """The validator must name the field, so a refusal that says nothing is not a refusal."""
    try:
        sr.refuse_if_deficient(deferred_no_id())
    except sr.DeferralContractError as exc:
        assert "covered_by" in str(exc), (
            "the refusal text does not name the deficient field covered_by: %r" % (str(exc),))
    else:
        raise AssertionError(
            "refuse_if_deficient accepted a deferral with covered_by empty; it must raise "
            "DeferralContractError naming the field")


def test_refuse_if_deficient_does_not_refuse_a_deferral_that_names_its_tracker():
    """The cost guard at the validator: a complete deferral passes, and returns None."""
    assert sr.refuse_if_deficient(deferral_with_id()) is None, (
        "refuse_if_deficient must return None for a deferral that names a task id and every "
        "t607 field")


def test_a_deferred_finding_with_no_task_id_is_not_written():
    """The write point is the defect: today _keep writes the row and records nothing.

    The text committed at 8f9d5ac0 asserted refusals(logs) == [] HERE, in the same file as a
    test asserting len(refusals(logs)) == 1 for the identical input -- no implementation can
    satisfy both, which is why that suite is retired. "Not written" is pinned here by the
    outcome that matters: the refused row is not logged as a written finding, so it is not
    counted as a terminal outcome.
    """
    new, covered, logs, payloads = run_keep([deferred_no_id()])
    assert payloads == {}, (
        "a kind=deferral row with covered_by empty was written to the findings dir; _keep "
        "must consult refuse_if_deficient before it writes: %r" % (payloads,))
    assert new == 0, "a refused deferral was counted as a new finding: new=%r" % (new,)
    assert covered == 0, "a refused deferral was counted as covered: covered=%r" % (covered,)
    assert finding_rows(logs) == [], (
        "the refused deferral was logged as a written finding, so it will be counted as a "
        "terminal outcome: %r" % (finding_rows(logs),))


def test_the_refusal_is_recorded_and_names_the_deficient_field():
    """A refused deferral that leaves no record is the 'correct and ignored' shape: the
    refusal is written to the log, once, and names the field and the quote."""
    new, covered, logs, payloads = run_keep([deferred_no_id()])
    rows = refusals(logs)
    assert len(rows) == 1, (
        "expected exactly one event=deferral-refused log row, got %r" % (logs,))
    row = rows[0]
    assert row.get("kind") == "deferral", "the refusal row lost its kind: %r" % (row,)
    assert QUOTE in str(row.get("quote", "")), "the refusal row lost its quote: %r" % (row,)
    assert "covered_by" in str(row.get("reason", "")), (
        "the refusal reason does not name the deficient field covered_by: %r" % (row,))
    assert str(row.get("key", "")), "the refusal row carries no record key: %r" % (row,)
    assert new == 0, "a refused deferral was counted as new: new=%r" % (new,)


def test_a_deferral_that_carries_a_task_id_is_accounted_not_dropped():
    """The id is the point: a deferral naming t1074 must become COVERED by t1074, not
    disappear and not be written again. That accounting is why the id exists."""
    new, covered, logs, payloads = run_keep([deferral_with_id()])
    assert refusals(logs) == [], (
        "a deferral that names task id %r was refused on the tracker field: %r"
        % (TID, refusals(logs)))
    assert covered == 1, (
        "a deferral naming task id %r was not counted as covered by it -- the finding "
        "disappeared: covered=%r new=%r" % (TID, covered, new))
    assert payloads == {}, (
        "a deferral that names a task id was written as a finding as well as counted as "
        "covered -- it must take exactly one path: %r" % (payloads,))


def test_a_deficient_deferral_does_not_stop_the_rest_of_its_part():
    """One bad row must not silently swallow the good rows in the same part."""
    new, covered, logs, payloads = run_keep([deferred_no_id(), unresolved_row()])
    assert len(payloads) == 1, (
        "expected exactly the unresolved row to be written, got %r" % (payloads,))
    body = list(payloads.values())[0]
    assert body.get("kind") == "unresolved", (
        "the row written for this part is not the unresolved finding: %r" % (body,))
    assert len(refusals(logs)) == 1, (
        "expected exactly one deferral-refused row alongside the written finding: %r" % (logs,))
    assert len(finding_rows(logs)) == 1, (
        "expected exactly one event=finding row (the unresolved finding) in this part: %r" % (logs,))
    assert new == 1, "expected new==1 (the unresolved finding only): new=%r" % (new,)


def test_a_non_deferral_with_no_tracker_is_still_written():
    """Cost guard: the fix must not turn into a blanket refusal of findings that lack ids."""
    new, covered, logs, payloads = run_keep([unresolved_row()])
    assert len(payloads) == 1, (
        "a kind=unresolved finding with covered_by empty must be written exactly as before: %r"
        % (payloads,))
    assert new == 1, "expected new==1: new=%r" % (new,)
    assert refusals(logs) == [], (
        "a non-deferral was logged as a deferral refusal: %r" % (refusals(logs),))


def _run():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:
            failed.append(name)
            print("FAIL %s: %s" % (name, exc))
        else:
            print("ok   %s" % name)
    print("%d/%d passed" % (len(tests) - len(failed), len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run())
