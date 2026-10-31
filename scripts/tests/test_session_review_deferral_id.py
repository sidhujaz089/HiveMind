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

Run: python3 scripts/tests/test_session_review_deferral_id.py  (pytest is not installed).
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
         "verify_command": "python3 scripts/tests/test_session_review_deferral_id.py",
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
    """The refusal must say WHICH field is deficient, in the words a person would act on."""
    try:
        sr.refuse_if_deficient(deferred_no_id())
    except sr.DeferralContractError as exc:
        msg = str(exc)
        assert "covered_by" in msg, (
            "the refusal text does not name the deficient field covered_by: %r" % (msg,))
    else:
        raise AssertionError(
            "refuse_if_deficient accepted a deferred finding with covered_by empty; the "
            "record on disk at workspace/session-reviews/findings/%s.json is exactly this "
            "shape" % KEY)


def test_refuse_if_deficient_does_not_refuse_a_deferral_that_names_its_tracker():
    try:
        sr.refuse_if_deficient(deferral_with_id())
    except sr.DeferralContractError as exc:
        raise AssertionError(
            "a deferral carrying a real task id was refused on the tracker field: %r"
            % (str(exc),))


def test_a_deferred_finding_with_no_task_id_is_not_written():
    """The write point is the defect: today _keep writes the row and records nothing."""
    new, covered, logs, payloads = run_keep([deferred_no_id()])
    assert payloads == {}, (
        "a kind=deferral row with covered_by empty was written to the findings dir; _keep "
        "must consult refuse_if_deficient before it writes: %r" % (payloads,))
    assert new == 0, "a refused deferral was counted as a new finding: new=%r" % (new,)
    assert covered == 0, "a refused deferral was counted as covered: covered=%r" % (covered,)
    assert refusals(logs) == [], (
        "a finding that was not written was logged as refused; write nothing, log nothing: %r"
        % (refusals(logs),))


def test_the_refusal_is_recorded_and_names_the_deficient_field():
    """A refusal that leaves no row is the 'correct and ignored' shape (RCA Rule 4).
    The row must say which record, and which field is missing, and why."""
    new, covered, logs, payloads = run_keep([deferred_no_id()])
    rows = refusals(logs)
    assert len(rows) == 1, (
        "expected exactly one event=deferral-refused log row, got %r" % (logs,))
    row = rows[0]
    assert row.get("kind") == "deferral", "the refusal row lost its kind: %r" % (row,)
    assert QUOTE in str(row.get("quote", "")), (
        "the refusal row does not carry the finding's quote, so nobody can find it: %r" % (row,))
    assert row.get("key") == sr._fkey(REQ_FILE.name, QUOTE), (
        "the refusal row does not carry the record key _keep computes for this quote: %r"
        % (row.get("key"),))
    reason = str(row.get("reason", ""))
    assert "covered_by" in reason, (
        "the refusal reason does not name the deficient field covered_by: %r" % (reason,))
    assert new == 0, "a refused deferral was counted as a new finding: new=%r" % (new,)


def test_a_deferral_that_carries_a_task_id_is_accounted_not_dropped():
    """Cost guard: naming a task id must make the finding COVERED by it, not discarded.
    That accounting is the whole point of the id (RCA: every surface keys on a task id)."""
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
