"""t1048: a deferral finding that names no tracker is refused AT THE WRITE POINT.

The contract ships tested and unconnected. scripts/session_review.py defines DEFERRAL_FIELDS
at :976, deferral_violations at :983 (it raises TypeError on a non-mapping :986, returns []
for any kind that is not 'deferral' :987, appends 'missing <field>' :993, and for tracked_as
requires a tNNN (re.fullmatch t\\d+ :1003) or 'UNTRACKED' with a non-empty reason :1005-1007)
and refuse_if_deficient at :1016 (raises DeferralContractError with '; '.join(violations)
:1020). grep -rn refuse_if_deficient --include=*.py finds it ONLY in scripts/tests/. _keep()
(:1024) writes every found row to FINDINGS/<key>.json (:1052-1058), logs a 'finding' event
(:1061) and counts it (:1063) -- it never consults the contract. That is why finding
70cf95eccc7a was filed for work committed six days earlier (workspace/rca/70cf95eccc7a.md).

This oracle pins the CALL SITE, not the validator: a kind=deferral row that fails the
contract is NOT written to the findings dir and IS recorded in the part's log with the reason;
a row that passes the contract, and every non-deferral row, are written exactly as before;
and one deficient row must not stop the rest of its part. Red on main for one reason: _keep()
writes the deficient row and records nothing.

Run: python3 scripts/tests/test_t1048_deferral_write_gate.py  (pytest is not installed).
No live state is touched: FINDINGS, _log and _now are redirected to a temp dir / fakes and
restored in a finally. No subprocess, no urlopen, no model, no process left running.
Do not invent a function, a module, or a file path: everything referenced is above.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr

# The real quote of the finding this task exists to stop (workspace/rca/70cf95eccc7a.md:20).
# Deliberately free of identifier-shaped tokens (no _, ., / or digit in any word of 6+ chars)
# so _archive_cover's `if not words: return ""` guard fires and the test reads no live task.
QUOTE = ("The Manager gets a task to run a root-cause analysis and decide, and it resets "
         "the switch itself")
REQ = {"id": "745a17ba", "harness": "claude", "_candidates": ["t613"]}
REQ_FILE = Path("claude-745a17ba-a559-4e99-81f6-3f8ec745ad46--from-74.json")
FIVE = ("tracked_as", "owner", "verify_command", "decay_test", "intended_state")


def bare_deferral(**over):
    """A deferral row carrying NONE of the five t607 fields (the 70cf95eccc7a shape)."""
    r = {"kind": "deferral",
         "quote": QUOTE,
         "why_missed": "Implement safety-switch RCA task that resets switch",
         "where": "assistant, turn 49 of 238"}
    r.update(over)
    return r


def full_deferral(**over):
    """A deferral row that satisfies the contract, with five DISTINCT real values."""
    r = bare_deferral(tracked_as="t613",
                      owner="Manager",
                      verify_command="python3 scripts/tests/test_freeze_circuit.py",
                      decay_test="a kind=deferral finding is written with no tracked_as field",
                      intended_state="no deferral reaches the RCA lane without naming its tracker")
    r.update(over)
    return r


def run_keep(found):
    """Call _keep() with every live-state surface redirected. Returns (new, covered, logs, payloads)."""
    tmp = Path(tempfile.mkdtemp(prefix="t1048-"))
    logs = []
    saved = (sr.FINDINGS, sr._log, sr._now)
    sr.FINDINGS = tmp / "findings"
    sr._log = lambda rec: logs.append(rec)
    sr._now = lambda: "2026-10-11T231514Z"
    try:
        new, covered = sr._keep(REQ_FILE, REQ, found, note_task=lambda *a, **k: None)
    finally:
        sr.FINDINGS, sr._log, sr._now = saved
        files = sorted((tmp / "findings").glob("*.json"))
        payloads = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in files}
        shutil.rmtree(tmp, ignore_errors=True)
    return new, covered, logs, payloads


def refusals(logs):
    return [l for l in logs if l.get("event") == "deferral-refused"]


def test_deferral_with_no_tracker_fields_is_not_written():
    """THE ROOT CAUSE: today this row is written and counted, and the log says nothing."""
    new, covered, logs, payloads = run_keep([bare_deferral()])
    assert payloads == {}, (
        "a kind=deferral row carrying none of %s was written to the findings dir -- "
        "_keep() must consult refuse_if_deficient() before writing" % (FIVE,))
    assert new == 0, "a refused deferral was counted as a new finding: new=%r" % (new,)
    assert covered == 0, "a refused deferral was counted as covered: covered=%r" % (covered,)
    # the refusal record itself is test 2's assertion (CLAUDE 2026-10-28: this line asserted zero rows and contradicted it)


def test_the_refusal_is_recorded_and_names_every_missing_field():
    """A refused deferral that leaves no record is the 'correct and ignored' shape (RCA Rule 4)."""
    new, covered, logs, payloads = run_keep([bare_deferral()])
    rows = refusals(logs)
    assert len(rows) == 1, (
        "expected exactly one event=deferral-refused log row, got %r" % (logs,))
    row = rows[0]
    assert row.get("kind") == "deferral", "the refusal row lost its kind: %r" % (row,)
    assert QUOTE in str(row.get("quote", "")), "the refusal row does not carry the quote: %r" % (row,)
    reason = str(row.get("reason", ""))
    for field in FIVE:
        assert field in reason, (
            "the refusal reason does not name the deficient field %r: %r" % (field, reason))
    assert new == 0, "a refused deferral was counted as new: new=%r" % (new,)


def test_a_deferral_that_names_its_tracker_is_written_unchanged():
    """The contract must not swallow deferrals that do name a tracker (t613 is a real tNNN)."""
    new, covered, logs, payloads = run_keep([full_deferral()])
    assert len(payloads) == 1, "a complete deferral was not written: %r" % (payloads,)
    body = list(payloads.values())[0]
    assert body["tracked_as"] == "t613", "tracked_as was not carried through: %r" % (body.get("tracked_as"),)
    assert body["owner"] == "Manager", "owner was not carried through: %r" % (body.get("owner"),)
    assert body["kind"] == "deferral", "kind was not carried through: %r" % (body.get("kind"),)
    assert new == 1, "a complete deferral was not counted as new: new=%r" % (new,)
    assert refusals(logs) == [], "a complete deferral was refused: %r" % (logs,)


def test_untracked_with_a_reason_is_written():
    """The loud escape the contract allows (deferral_violations :1005-1007): a false positive
    must stay checkable, so a deferral that states WHY it has no tracker is still filed."""
    r = full_deferral(tracked_as="UNTRACKED: no tNNN named in any turn after the quote")
    new, covered, logs, payloads = run_keep([r])
    assert len(payloads) == 1, "UNTRACKED with a reason was refused: %r" % (logs,)
    assert refusals(logs) == [], "UNTRACKED with a reason must not be refused: %r" % (logs,)
    assert new == 1, "UNTRACKED with a reason was not counted: new=%r" % (new,)


def test_bare_untracked_with_no_reason_is_refused_by_name():
    """Pins the validator's real semantics, so presence-only checks cannot pass."""
    r = full_deferral(tracked_as="UNTRACKED")
    new, covered, logs, payloads = run_keep([r])
    assert payloads == {}, "tracked_as='UNTRACKED' with no reason was written: %r" % (payloads,)
    rows = refusals(logs)
    assert len(rows) == 1, "expected one refusal row, got %r" % (logs,)
    assert "tracked_as" in str(rows[0].get("reason", "")), (
        "the refusal did not name tracked_as: %r" % (rows[0],))


def test_a_non_deferral_row_is_never_touched_by_the_contract():
    """The negation guard: refusing every kind is not the contract. deferral_violations
    returns [] for any kind that is not 'deferral' (:987), so this row must be written."""
    new, covered, logs, payloads = run_keep([bare_deferral(kind="noticed")])
    assert len(payloads) == 1, "a kind=noticed row was refused: %r" % (payloads,)
    assert refusals(logs) == [], "a non-deferral row was logged as refused: %r" % (logs,)
    assert new == 1, "a kind=noticed row was not counted: new=%r" % (new,)


def test_one_deficient_deferral_does_not_stop_the_rest_of_its_part():
    """Pins the shape of the handler: a raise escaping _keep() aborts the loop at :1029 and
    silently drops every later finding in the part. _keep() must return (1, 0) here."""
    new, covered, logs, payloads = run_keep([bare_deferral(), bare_deferral(kind="noticed")])
    assert len(payloads) == 1, (
        "the good row was not written alongside the deficient one: %r" % (payloads,))
    assert refusals(logs) and new == 1, (
        "new=%r refusals=%r: a deficient deferral must be refused and its siblings kept" % (new, refusals(logs)))


if __name__ == "__main__":
    _tests = sorted((v for k, v in globals().items()
                     if k.startswith("test_") and callable(v)), key=lambda f: f.__name__)
    _failed = 0
    for _fn in _tests:
        try:
            _fn()
            print("PASS", _fn.__name__)
        except Exception as _e:
            _failed += 1
            print("FAIL", _fn.__name__, "->", type(_e).__name__, _e)
    print("%d/%d passed" % (len(_tests) - _failed, len(_tests)))
    sys.exit(1 if _failed else 0)
