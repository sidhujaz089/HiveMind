#!/usr/bin/env python3
r"""t1276 -- a deferral is a deferral only when the work is NOT yet done.

THE CASE THAT STARTED IT (workspace/rca/ec9e2fe12ab3.md, read today):

  quote:   "On your last point — I'll keep Q4's own position out of the brief entirely."
  context: "Approved and executing. It's a research task; the researcher's future is
            irrelevant to it, and raising it could only distort the results."
  record:  workspace/session-reviews/findings/ec9e2fe12ab3.json --
           kind: deferral, disposition: deferred, covered_by: ""

The sentence is a standing decision that was already executing. The extractor read the modal
verb ("I'll keep ... out") and never asked the one question that decides the kind: is the
action a future promise, or is it in force? scripts/session_review.py:936 then projects the
disposition from the kind -- _KIND_DISPOSITION = {"deferral": "deferred", ...} -- so the
record cannot say "executed" even when the artefact proves it, and a false deferral is an
open loose end that gets re-dispatched for RCA forever.

This suite pins the code-side answer, hermetically: strings in, strings out, plus one pass
through _keep with every side effect pointed at a temp dir. No model call, no server, no
subprocess, nothing written outside a temp dir.

RED ON MAIN FOR THE STATED REASON, AND ONLY FOR IT: session_review has no EXECUTION_STATUSES,
EXECUTION_EVIDENCE, deferral_execution_status or deferral_disposition (grep finds neither name
in the file), so every test below raises AttributeError on that attribute; the _keep tests are
additionally red because the finding _keep writes carries no execution_status and it files a
deferral ledger row for an in-force decision. The file imports cleanly today (the module is
imported and run by scripts/tests/test_session_review_live_state.py), so a failure is the
missing change, never an import path.

WHAT THIS SUITE DELIBERATELY DOES NOT ASK FOR. It does not add a sixth DISPOSITIONS value.
scripts/tests/test_session_review_verification_claims.py:221 asserts len(DISPOSITIONS) == 5
and is GREEN on main (run today: 13/13). A brief that adds "in-force" makes that sibling
suite -- outside its own file, "MUST STAY GREEN" in its own brief -- go red: the exact shape
workspace/tasks/t1230--harness-dispatch-does-not-verify-preconditions-b.md records, and the
shape scripts/preflight_brief.py's precondition_findings exists to stop. Every disposition
this suite expects is one of the five that exist today; test_05 is the ratchet that keeps it
so. If Jas later wants a sixth value, that is a separate scope that relaxes that assertion in
the same commit as the vocabulary -- see the task's For Claude.

The second RCA's evidence rows (652/685/695-696/751/759 of session e45bca10) are a note on
the task, not a fixture: that transcript has not been read, so KEPT_CONTEXT below is a
one-line restatement of what RCA 06663ada90b1 records (the harness was built at row 685 and
measured by row 696). KEPT_QUOTE is verbatim from workspace/tasks/t1276--...md.

Run: python3 scripts/tests/test_session_review_deferral_execution_status.py
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr

# ---- the canonical in-force decision: both strings verbatim from workspace/rca/ec9e2fe12ab3.md
DECIDED_QUOTE = "On your last point — I'll keep Q4's own position out of the brief entirely."
DECIDED_CONTEXT = ("Approved and executing. It's a research task; the researcher's future is "
                   "irrelevant to it, and raising it could only distort the results.")

# ---- a promise the session kept: quote verbatim (task body), context a restatement (see docstring)
KEPT_QUOTE = "I'll grade lexical and semantic side by side in one harness instead."
KEPT_CONTEXT = ("Built that harness later in the session, at row 685, and measured both "
                "scorers by row 696.")

# ---- promises nobody came back to: the class the fix must keep catching
OPEN_QUOTE_A = "I'll write the decay test for that tomorrow, once this build is green."
OPEN_CONTEXT_A = "Noted; the session ends here and no task number was given."
OPEN_QUOTE_B = "I'll add the carry to the skill after the review lands."
OPEN_CONTEXT_B = "Agreed -- park it for now and file nothing."

# The five dispositions legal on main today. This change adds none; the classifier may only
# speak these words (scripts/tests/test_session_review_verification_claims.py:221 fixes the count).
TODAY = ("unresolved", "resolved-in-session", "deferred", "for-jas", "refuted")

IN_FORCE_ROW = {"kind": "deferral", "quote": DECIDED_QUOTE, "context": DECIDED_CONTEXT,
                "where": "assistant, turn 595 of 602",
                "why_missed": "Exclude Q4's position from research brief",
                "tracked_as": "UNTRACKED: session-review deferral with no owner recorded"}
OPEN_ROW = {"kind": "deferral", "quote": OPEN_QUOTE_A, "context": OPEN_CONTEXT_A,
            "where": "assistant, late in the session",
            "why_missed": "no task number was given"}


def _keep_run(found):
    """Call _keep with every side effect redirected into a temp dir.

    Returns (records, deferred_items, logs). _keep writes three things outside its arguments:
    the module's FINDINGS directory, _log (which appends to the live review log) and
    record_deferral (whose default ledger is workspace/deferrals.jsonl). All three are replaced
    for the call and restored in a finally, so a run of this suite changes nothing in the tree.
    """
    saved = (sr.FINDINGS, sr._log, sr.record_deferral)
    logs, deferred = [], []
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        sr.FINDINGS = tmp / "findings"

        def _fake_log(row):
            logs.append(row)

        def _fake_record(item, reason, closer, session, ledger=None):
            deferred.append(item)
            return {"item": item, "reason": reason, "closer": closer, "session": session}

        sr._log = _fake_log
        sr.record_deferral = _fake_record
        req = {"id": "sess-t1276", "harness": "claude", "_candidates": []}
        req_file = tmp / "req-t1276.json"
        req_file.write_text(json.dumps(req), encoding="utf-8")
        try:
            sr._keep(req_file, req, [dict(r) for r in found],
                     note_task=lambda *a: None, file_task=lambda *a: None)
        finally:
            sr.FINDINGS, sr._log, sr.record_deferral = saved
        records = [json.loads(p.read_text(encoding="utf-8"))
                   for p in sorted((tmp / "findings").glob("*.json"))]
    return records, deferred, logs


def test_01_the_execution_vocabulary_is_declared():
    st = sr.EXECUTION_STATUSES
    assert isinstance(st, tuple), "EXECUTION_STATUSES must be a tuple, got %r" % (type(st),)
    assert len(st) == 3 and len(set(st)) == 3, "expected 3 distinct statuses, got %r" % (st,)
    assert "not-yet-done" in st, "the default status must be named: %r" % (st,)
    assert all(isinstance(s, str) and s.strip() for s in st), "statuses must be non-empty strings: %r" % (st,)

    ev = sr.EXECUTION_EVIDENCE
    assert isinstance(ev, tuple) and len(ev) >= 1, "EXECUTION_EVIDENCE must be a non-empty tuple, got %r" % (ev,)
    assert all(isinstance(m, str) and m.strip() for m in ev), "every marker must be a non-empty string: %r" % (ev,)
    assert all(m == m.lower() for m in ev), "markers are matched case-insensitively; store them lowercase: %r" % (ev,)
    # Non-vacuity: the constant must fire on the REAL context the RCA recorded, not on a
    # phrase nobody uses. An EXECUTION_EVIDENCE of unmatchable strings passes every other
    # assertion in this file and catches nothing -- this is the assertion that stops it.
    assert any(m in DECIDED_CONTEXT.lower() for m in ev), (
        "no EXECUTION_EVIDENCE marker appears in the canonical RCA context, so the list "
        "matches nothing in the artefact it exists to read: %r" % (ev,))


def test_02_a_decision_already_in_force_is_not_a_deferral():
    assert sr.deferral_execution_status(DECIDED_QUOTE, DECIDED_CONTEXT) == "decision-in-force", (
        "the RCA's own sentence, said over 'Approved and executing', must classify as a "
        "standing decision, not a future promise; got %r"
        % (sr.deferral_execution_status(DECIDED_QUOTE, DECIDED_CONTEXT),))
    assert sr.deferral_disposition("deferral", DECIDED_QUOTE, DECIDED_CONTEXT) == "resolved-in-session"


def test_03_a_promise_the_session_kept_is_not_a_deferral():
    assert sr.deferral_execution_status(KEPT_QUOTE, KEPT_CONTEXT) == "executed-in-session", (
        "a promise whose action the context records as built and measured is "
        "executed-in-session; got %r"
        % (sr.deferral_execution_status(KEPT_QUOTE, KEPT_CONTEXT),))
    assert sr.deferral_disposition("deferral", KEPT_QUOTE, KEPT_CONTEXT) == "resolved-in-session"


def test_04_a_promise_nobody_came_back_to_is_still_a_deferral():
    # The negative half: a fix that swallows real deferrals is worse than no fix at all.
    for q, c in ((OPEN_QUOTE_A, OPEN_CONTEXT_A), (OPEN_QUOTE_B, OPEN_CONTEXT_B)):
        got = sr.deferral_execution_status(q, c)
        assert got == "not-yet-done", "an unkept promise must stay a deferral, got %r for %r" % (got, c)
        assert sr.deferral_disposition("deferral", q, c) == "deferred"


def test_05_the_disposition_uses_only_the_words_that_exist_today():
    for kind, q, c, want in (
            ("deferral", DECIDED_QUOTE, DECIDED_CONTEXT, "resolved-in-session"),
            ("deferral", KEPT_QUOTE, KEPT_CONTEXT, "resolved-in-session"),
            ("deferral", OPEN_QUOTE_A, OPEN_CONTEXT_A, "deferred"),
            ("deferral", OPEN_QUOTE_B, OPEN_CONTEXT_B, "deferred"),
            ("unresolved", OPEN_QUOTE_A, OPEN_CONTEXT_A, "unresolved"),
            ("for-jas", OPEN_QUOTE_B, OPEN_CONTEXT_B, "for-jas"),
            # only deferrals are re-classified: a settled bug is not this function's to move
            ("unresolved", DECIDED_QUOTE, DECIDED_CONTEXT, "unresolved"),
            ("for-jas", DECIDED_QUOTE, DECIDED_CONTEXT, "for-jas"),
    ):
        got = sr.deferral_disposition(kind, q, c)
        assert got == want, "deferral_disposition(%r) should be %r, got %r" % (kind, want, got)
        assert got in TODAY, "returned %r, which is not a legal disposition today" % (got,)
    for value in TODAY:
        assert value in sr.DISPOSITIONS, "value %r that is legal today must stay legal: %r" % (value, sr.DISPOSITIONS)


def test_06_absence_of_evidence_never_clears_a_promise():
    # The safe direction, pinned: no context, a non-string context and a context that only
    # notes the promise all stay a deferral. A build that defaults to in-force fails here.
    for c in ("", None, 0, "Noted for later.", OPEN_CONTEXT_A):
        assert sr.deferral_execution_status(DECIDED_QUOTE, c) == "not-yet-done", (
            "context %r carries no evidence of execution, so the promise stands: got %r"
            % (c, sr.deferral_execution_status(DECIDED_QUOTE, c)))
    assert sr.deferral_disposition("deferral", DECIDED_QUOTE, "") == "deferred"


def test_07_keep_records_the_in_force_decision_and_stops_filing_it_as_a_deferral():
    records, deferred, logs = _keep_run([IN_FORCE_ROW])
    assert len(records) == 1, "expected exactly one finding written, got %r" % (records,)
    rec = records[0]
    assert rec.get("quote") == DECIDED_QUOTE
    assert rec.get("execution_status") == "decision-in-force", (
        "the finding must be able to state the state (RCA 5): got %r" % (rec.get("execution_status"),))
    assert rec.get("disposition") == "resolved-in-session", (
        "a standing decision must not be filed as deferred, which is what made "
        "findings/ec9e2fe12ab3.json an open loose end: got %r" % (rec.get("disposition"),))
    assert rec.get("disposition") in sr.DISPOSITIONS
    ev = rec.get("evidence")
    assert isinstance(ev, str) and "Approved and executing" in ev, (
        "the record must cite the words that say so, whole: %r" % (ev,))
    assert deferred == [], "an in-force decision was entered in the deferral ledger: %r" % (deferred,)


def test_08_keep_still_files_a_real_deferral_as_a_deferral():
    records, deferred, logs = _keep_run([OPEN_ROW])
    assert len(records) == 1, "expected exactly one finding written, got %r" % (records,)
    rec = records[0]
    assert rec.get("disposition") == "deferred", "a real deferral must stay deferred: %r" % (rec.get("disposition"),)
    assert rec.get("execution_status") == "not-yet-done"
    assert rec.get("evidence", "") == "", "a deferral with no evidence must not gain one: %r" % (rec.get("evidence"),)
    assert deferred == [OPEN_QUOTE_A], "the real deferral must still reach the ledger: %r" % (deferred,)


def test_09_one_part_with_both_rows_files_each_its_own_way():
    # A fix that resolves every deferral it sees, or that drops the sibling record, is caught.
    records, deferred, logs = _keep_run([IN_FORCE_ROW, OPEN_ROW])
    by_quote = {r.get("quote"): r for r in records}
    assert set(by_quote) == {DECIDED_QUOTE, OPEN_QUOTE_A}, "both findings must be written: %r" % (list(by_quote),)
    assert by_quote[DECIDED_QUOTE]["disposition"] == "resolved-in-session"
    assert by_quote[OPEN_QUOTE_A]["disposition"] == "deferred"
    assert deferred == [OPEN_QUOTE_A], (
        "exactly one deferral row, for the promise nobody came back to: %r" % (deferred,))
    assert by_quote[OPEN_QUOTE_A].get("execution_status") == "not-yet-done"
    assert by_quote[DECIDED_QUOTE].get("execution_status") == "decision-in-force"


TESTS = (
    test_01_the_execution_vocabulary_is_declared,
    test_02_a_decision_already_in_force_is_not_a_deferral,
    test_03_a_promise_the_session_kept_is_not_a_deferral,
    test_04_a_promise_nobody_came_back_to_is_still_a_deferral,
    test_05_the_disposition_uses_only_the_words_that_exist_today,
    test_06_absence_of_evidence_never_clears_a_promise,
    test_07_keep_records_the_in_force_decision_and_stops_filing_it_as_a_deferral,
    test_08_keep_still_files_a_real_deferral_as_a_deferral,
    test_09_one_part_with_both_rows_files_each_its_own_way,
)

if __name__ == "__main__":
    failed = 0
    for fn in TESTS:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:                       # a missing attribute is the missing change
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % fn.__name__)
    print("t1276 deferral execution status: %d of %d case(s) failed" % (failed, len(TESTS)))
    sys.exit(1 if failed else 0)
