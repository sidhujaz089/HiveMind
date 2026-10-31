#!/usr/bin/env python3
'''t1355: deferral 52ef07b1aae6 is discharged by landed commit 1734764, not by a task.

The RCA (workspace/rca/52ef07b1aae6.md) closed causes C1/C2 with evidence: the promise at
transcript turn 235 was executed in the very next tool call -- scripts/session_end.py:872 is the
line `text=True, timeout=7200)` -- and committed as 1734764, so the obligation was never deferred.
The finding record (workspace/session-reviews/findings/done/52ef07b1aae6.json) says otherwise:
its only discharge pointer is a task id ("covered_by": "t1266", 52ef07b1aae6.json:7; "outcome_task":
"t1266", 52ef07b1aae6.json:909) and it carries no resolution_status and no resolution_evidence, so
a commit-discharged promise reads exactly like an abandoned one. This suite is RED while that is
true, and it stays red for the class: a record that discharges a promise by naming a task id, or
that cites a session close-out commit as its cover, fails it.

The status names are the enum scripts/tests/test_deferral_resolution_status.py:7-8 pins --
OPEN / MET-IN-SESSION / MET-LATER / NOT-MET. There is no "done"; a promise kept in the turn it was
spoken is MET-IN-SESSION. That suite is red on main (deferral_violations at scripts/session_review.py:1069
validates only the t607 fields), so nothing here imports it: the assertions below are about this
record, not about code t1266 has not landed.

No model, no live server, no process left running, no fixed /tmp path, no write to the repository:
this file reads the finding record and asks git for one commit subject.
'''
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RECORD = REPO / "workspace" / "session-reviews" / "findings" / "done" / "52ef07b1aae6.json"
COMMIT = "1734764"
RESOLVED = ("MET-IN-SESSION", "MET-LATER")
QUOTE = ("Rather than argue it was already there, I'll fix it properly by "
         "giving the call an explicit timeout:")
SESSION = "be2121ac-5236-4268-a5fa-56bda26324e3"
RCA = "workspace/rca/52ef07b1aae6.md"


def _record():
    assert RECORD.is_file(), "the finding record is missing: %s" % RECORD
    return json.loads(RECORD.read_text(encoding="utf-8"))


def test_record_is_the_finding_this_task_closes():
    rec = _record()
    assert rec.get("key") == "52ef07b1aae6", rec.get("key")
    assert rec.get("kind") == "deferral", rec.get("kind")
    # Identity is pinned by the real quote, so a build cannot discharge the deferral by rewriting
    # the record into something else.
    assert rec.get("quote") == QUOTE, repr(rec.get("quote"))


def test_resolution_status_records_a_promise_kept_in_session():
    rec = _record()
    status = rec.get("resolution_status")
    assert status in RESOLVED, (
        "resolution_status is %r; the fix landed in the same turn as the promise, so it is %r "
        "-- not OPEN, not NOT-MET, not absent" % (status, RESOLVED[0]))


def test_resolution_evidence_is_the_landed_commit_not_a_task_id():
    rec = _record()
    evidence = (rec.get("resolution_evidence") or "").strip()
    assert evidence, "resolution_evidence is empty: a resolved deferral must name what discharged it"
    assert COMMIT in evidence, (
        "resolution_evidence %r must cite commit %s, the RCA's artefact for the timeout=7200 fix "
        "at scripts/session_end.py:872" % (evidence, COMMIT))
    assert not re.fullmatch(r"t\d+", evidence), (
        "resolution_evidence %r is a bare task id: that is the defect t1355 exists to fix -- a "
        "promise discharged by a commit resolved only against task ids" % evidence)


def test_the_cited_commit_is_real_and_not_a_session_closeout():
    # A record may not claim a discharge commit that does not exist, or one that only records a
    # session: prove_cover() at scripts/session_review.py:758 is the shipped rule that a close-out
    # fixes nothing.
    r = subprocess.run(["git", "-C", str(REPO), "log", "-1", "--format=%s", COMMIT],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, "git cannot resolve commit %s: %s" % (COMMIT, r.stderr.strip())
    subject = r.stdout.strip()
    assert subject, "commit %s has no subject -- not a real commit" % COMMIT
    assert not subject.startswith("session:"), (
        "commit %s is a session close-out (%r), which records a session and fixes nothing"
        % (COMMIT, subject))


def test_the_edit_kept_the_existing_record_intact():
    rec = _record()
    for field in ("key", "kind", "quote", "session", "found", "rca_report"):
        assert rec.get(field), "field %r was dropped from the finding record" % field
    assert rec.get("session") == SESSION, rec.get("session")
    assert rec.get("rca_report") == RCA, rec.get("rca_report")
    # The fix ADDS the resolution fields; it does not replace the task pointer that is already there.
    assert rec.get("covered_by") == "t1266", rec.get("covered_by")
    assert rec.get("outcome_task") == "t1266", rec.get("outcome_task")
    # 893 ids (the list is lines 15-907 of the record): a builder re-emitting this 913-line file
    # must not quietly shorten the cover-search row.
    assert len(rec.get("candidates_consulted") or []) == 893, len(rec.get("candidates_consulted") or [])


if __name__ == "__main__":
    failures = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
            except AssertionError as e:
                failures += 1
                print("FAIL", _name, e)
    if failures:
        print("%d test(s) failed" % failures)
        sys.exit(1)
    print("ok: deferral 52ef07b1aae6 is discharged by commit 1734764")
