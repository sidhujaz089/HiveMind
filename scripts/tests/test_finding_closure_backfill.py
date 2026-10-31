#!/usr/bin/env python3
"""t1459 checklist (a): back-fill finding fb7d098621b5 with its closing-commit evidence.

Run standalone:

    python3 scripts/tests/test_finding_closure_backfill.py

WHY.  workspace/rca/fb7d098621b5.md established that nothing joins this finding to the commit that
closed its subject.  The task states the remedy twice: "Back-fill this record: key=fb7d098621b5,
sha=91ad97f5, file=projects/local-inference/methodology.py" and checklist (a) "back-fill fb7d098621b5
with 91ad97f5 as its closure evidence ... recording disposition=resolved-in-session with the sha".
The record on disk carries neither the sha nor that disposition, so a reader of the finding cannot
find the commit and a reader of the commit cannot find the finding: the join this task exists to make.

READ-OFF-DISK FACTS THIS SUITE IS BUILT ON (each verified by reading the file, so no assertion here
rests on a guess):
  * workspace/session-reviews/findings/done/fb7d098621b5.json exists, 968 lines / 12,273 bytes, and
    has exactly 17 top-level keys: candidates_consulted, completed_at, context, covered_by,
    disposition, found, harness, key, kind, outcome_status, outcome_task, quote, rca_report, request,
    session, where, why_missed.  None of them is "closure", so every closure assertion below is RED
    on main today and red for the missing back-fill and for nothing else.
  * Its disposition is "covered" and its why_missed is the literal "never mentioned again; no
    covering task found" -- the exact string checklist (b) retires from stored records.
  * Its completed_at is "2026-10-18T16:04:05Z", which is the `at` value the closure evidence reuses.
  * The full sha of 91ad97f5 is 91ad97f58d833bf8d4082707cf2a708107470412 (git rev-parse 91ad97f5).
    Commit 91ad97f5's MESSAGE names methodology.py (git show --stat 91ad97f5), which is why the
    method is 'message-anchor' and not 'diff-path'.
  * workspace/session-reviews/findings/fb7d098621b5.json does NOT exist: ls of that directory shows
    only the done/ subdirectory, so "move it out of the unresolved queue" needs no file move.

This suite READS the real record and writes NOTHING: no temp dirs are needed, no live state is
touched, no subprocess, no network, no model call.  Its input is the record on disk, which is what
keeps the check from going vacuous -- the task's own requirement.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "workspace" / "session-reviews" / "findings" / "done" / "fb7d098621b5.json"
RECORD_IN_QUEUE = ROOT / "workspace" / "session-reviews" / "findings" / "fb7d098621b5.json"

# Real values, each read off disk or out of git this session, so no assertion can be satisfied by
# a placeholder and no two assertions share one placeholder string.
KEY = "fb7d098621b5"
CLOSING_SHA = "91ad97f58d833bf8d4082707cf2a708107470412"
SUBJECT_FILE = "projects/local-inference/methodology.py"
METHOD = "message-anchor"
TASK_ID = "t1459"
AT = "2026-10-18T16:04:05Z"
RCA_REPORT = "workspace/rca/fb7d098621b5.md"
CLOSURE_FIELDS = {"at", "sha", "file", "method", "task_id"}
RETIRED_WHY_MISSED = "never mentioned again; no covering task found"
DEAD_DISPOSITION = "covered"


def record():
    """The finding as it sits on disk.  No synthetic fallback: the back-fill's target IS this file."""
    if not RECORD.is_file():
        raise AssertionError(
            "the finding record is not on disk at %s -- the back-fill's target is the record on "
            "disk, so this suite refuses to fall back to a synthetic copy of it" % RECORD)
    with open(RECORD, encoding="utf-8") as handle:
        parsed = json.load(handle)
    assert isinstance(parsed, dict), (
        "the record must stay a JSON object that json.load parses; it parsed as %s"
        % type(parsed).__name__)
    return parsed


def closure_of(rec):
    closure = rec.get("closure")
    assert isinstance(closure, dict), (
        "the record has no 'closure' object (found %r).  The back-fill must record the closing "
        "commit's evidence as a structured object, because a reader looks the sha up BY FIELD.  A "
        "bare sha string anywhere in the record would pass a substring search and leave the join "
        "unqueryable." % (closure,))
    return closure


def test_the_record_is_archived_out_of_the_unresolved_queue():
    rec = record()
    assert rec.get("key") == KEY, (
        "the record at %s carries key %r, not %r -- this is not the record t1459 back-fills"
        % (RECORD.name, rec.get("key"), KEY))
    assert not RECORD_IN_QUEUE.exists(), (
        "a second copy of the record exists at %s.  The same finding would then read as OPEN to "
        "anything scanning findings/ and as CLOSED to anything scanning findings/done/ -- the "
        "split checklist (a) exists to remove." % RECORD_IN_QUEUE)


def test_the_record_records_the_closing_sha_as_closure_evidence():
    closure = closure_of(record())
    assert set(closure) == CLOSURE_FIELDS, (
        "closure's fields must be exactly the join row's fields (less key, which is the record's "
        "own name) so a reader of this record and a reader of workspace/findings-closure.jsonl "
        "read one schema: expected %s, found %s"
        % (sorted(CLOSURE_FIELDS), sorted(closure)))
    assert closure.get("sha") == CLOSING_SHA, (
        "closure.sha must be the full sha of the closing commit, %r, got %r.  A reader passes it "
        "straight to git show, so a short prefix is not a join." % (CLOSING_SHA, closure.get("sha")))
    assert closure.get("sha") != "91ad97f5", (
        "closure.sha is the abbreviated prefix.  The task names 91ad97f5 as the commit; the record "
        "must carry the sha a reader can resolve without guessing at an abbreviation.")


def test_the_closure_evidence_names_the_subject_file_the_method_and_the_task():
    closure = closure_of(record())
    assert closure.get("file") == SUBJECT_FILE, (
        "closure.file must be exactly %r, the file the closing commit's message names, got %r"
        % (SUBJECT_FILE, closure.get("file")))
    assert closure.get("method") == METHOD, (
        "closure.method must be %r, got %r.  The RCA's whole distinction is that commit 91ad97f5 "
        "names the file in its MESSAGE -- verified: git show 91ad97f5 says 'methodology.py set no "
        "num_ctx at all' -- and NOT in its diff.  Recording 'diff-path' asserts a join the commit "
        "does not support." % (METHOD, closure.get("method")))
    assert closure.get("method") != "diff-path", (
        "see the previous assertion: 'diff-path' claims the path was in the commit's diff, which "
        "is the claim the RCA says cannot be recovered.")
    assert closure.get("task_id") == TASK_ID, (
        "closure.task_id must be %r, the task that back-fills this record, got %r"
        % (TASK_ID, closure.get("task_id")))
    assert closure.get("at") == AT, (
        "closure.at must be the record's existing completed_at, %r, got %r -- the closure is dated "
        "to the review that found the finding, not to a date invented here."
        % (AT, closure.get("at")))
    assert re.fullmatch(r"[0-9a-f]{40}", str(closure.get("sha"))) is not None, (
        "closure.sha is not 40 hex characters, so no tool can pass it to git show")


def test_the_record_is_marked_resolved_in_session():
    rec = record()
    assert rec.get("disposition") == "resolved-in-session", (
        "disposition is %r.  Checklist (a) asks for disposition=resolved-in-session recorded with "
        "the sha, because this finding was closed by a commit made inside the reviewed session, not "
        "by a later task." % (rec.get("disposition"),))
    assert rec.get("disposition") != DEAD_DISPOSITION, (
        "disposition must not stay %r: 'covered' sends the reader to task t735 and says no commit "
        "closed the finding -- the gap workspace/rca/fb7d098621b5.md was written for."
        % (DEAD_DISPOSITION,))


def test_the_back_fill_kept_every_field_a_reader_of_the_record_uses():
    rec = record()
    for field in ("key", "kind", "quote", "context", "where", "covered_by", "outcome_task",
                  "outcome_status", "candidates_consulted", "request", "session", "harness",
                  "found", "completed_at", "rca_report"):
        assert field in rec, (
            "the back-fill dropped %r, a key the record carries on main today.  Provenance is the "
            "reason the finding is auditable: adding closure evidence must not erase it." % field)
    assert rec.get("key") == KEY, "key must stay %r, got %r" % (KEY, rec.get("key"))
    assert rec.get("rca_report") == RCA_REPORT, (
        "rca_report must stay %r, got %r" % (RCA_REPORT, rec.get("rca_report")))
    assert rec.get("completed_at") == AT, (
        "completed_at must stay %r, got %r" % (AT, rec.get("completed_at")))
    assert isinstance(rec.get("candidates_consulted"), list) and rec.get("candidates_consulted"), (
        "candidates_consulted must stay the record's non-empty list of tasks consulted, got %r"
        % (rec.get("candidates_consulted"),))
    assert rec.get("why_missed") != RETIRED_WHY_MISSED, (
        "why_missed is still the literal %r.  Checklist (b) retires that string from stored "
        "records, and this is the record the RCA was written for: once the join is recorded, it "
        "must say what closed the finding." % (RETIRED_WHY_MISSED,))


if __name__ == "__main__":
    cases = sorted((n, f) for n, f in list(globals().items())
                   if n.startswith("test_") and callable(f))
    failures = 0
    for name, fn in cases:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:
            failures += 1
            print("ERROR %s: %s: %s" % (name, type(exc).__name__, exc))
    print("%d/%d passed" % (len(cases) - failures, len(cases)))
    sys.exit(1 if failures else 0)
