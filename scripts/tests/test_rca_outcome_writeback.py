"""t940 oracle: the RCA outcome must land on the finding record, and a key whose
owner task is done must refuse a second RCA dispatch.

Red on main for the missing change: scripts/session_review.py defines no
task_status_from_disk / write_rca_outcome / rca_outcome_resolved, and the RCA
completion path writes only the ledger row (session_review.py:1262). The finding
record for 028b97d00e2a on disk carries covered_by "" / disposition "deferred" and
no outcome field, and the same key was dispatched for RCA again at 09:20:20Z
(workspace/rca/028b97d00e2a.md sections 1 and 2.4).

No pytest (not installed): run as `python3 scripts/tests/test_rca_outcome_writeback.py`.
No live state is touched: every path is under tempfile.mkdtemp().
"""
import inspect
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

KEY = "028b97d00e2a"
QUOTE = ("Once that lands, `scope_queue`'s rows will actually be classified by the "
         "inventory instead of sliding past as `not-applicable`, and the gate will "
         "bite for the first time.")
REPORT = "workspace/rca/%s.md" % KEY

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("FAIL: " + msg)
    return bool(cond)


def finding_record():
    """The real shape of workspace/session-reviews/findings/done/028b97d00e2a.json."""
    return {
        "kind": "deferral",
        "quote": QUOTE,
        "context": "Producer-path dispatch - the other half.",
        "where": "assistant, turn 88 of 395",
        "why_missed": "never mentioned again; no covering task found",
        "covered_by": "",
        "overruled": "unproven cover t235: shares 0.08 of the item's words (< 0.2)",
        "disposition": "deferred",
        "key": KEY,
        "request": "claude-721de5a0-effc-479b-bb75-99ca00223ee9.json",
        "session": "721de5a0-effc-479b-bb75-99ca00223ee9",
        "harness": "claude",
        "found": "2026-10-08T07:15:17Z",
        "candidates_consulted": ["t235", "t804", "t791"],
    }


def task_file(tasks_dir, tid, status):
    d = Path(tasks_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s--the-harness-inventory-is-blind.md" % tid)
    p.write_text(
        "---\n"
        "id: %s\n"
        "title: The harness inventory is blind to indirect model callers\n"
        "area: workspace\n"
        "kind: unfiled\n"
        "status: %s\n"
        "source: session-rca\n"
        "created: 2026-09-08T130834Z\n"
        "---\n\n"
        "Notes: evidence.status('scripts/scope_queue.py') answers not-applicable.\n"
        % (tid, status),
        encoding="utf-8")
    return p


def write_finding(root, rec):
    p = root / (KEY + ".json")
    p.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return p


def test_writer_records_the_outcome_and_reads_status_from_disk():
    import session_review
    root = Path(tempfile.mkdtemp())
    try:
        fpath = write_finding(root, finding_record())
        tasks = root / "tasks"
        task_file(tasks, "t235", "done")
        rec = session_review.write_rca_outcome(
            fpath, outcome_task="t235", rca_report=REPORT,
            disposition="covered", completed_at="2026-10-08T07:59:21Z",
            tasks_dir=tasks)
        disk = json.loads(fpath.read_text(encoding="utf-8"))
        check(rec["outcome_task"] == "t235", "outcome_task not recorded: %r" % (rec.get("outcome_task"),))
        check(rec["outcome_status"] == "done",
              "outcome_status must be READ from the owner task's file, got %r" % (rec.get("outcome_status"),))
        check(disk["outcome_status"] == "done", "outcome_status not on disk")
        check(disk["outcome_task"] == "t235", "outcome_task not on disk")
        check(disk["rca_report"] == REPORT, "rca_report not on disk: %r" % (disk.get("rca_report"),))
        check(disk["completed_at"] == "2026-10-08T07:59:21Z", "completed_at not on disk: %r" % (disk.get("completed_at"),))
        check(disk["disposition"] == "covered", "disposition not updated: %r" % (disk.get("disposition"),))
        check(disk["quote"] == QUOTE, "writer rewrote the quote: update in place, not replace")
        check(disk["key"] == KEY, "writer dropped the key")
        check(disk["kind"] == "deferral", "writer rewrote kind")
        check(disk["candidates_consulted"] == ["t235", "t804", "t791"], "writer dropped candidates_consulted")
        check(str(disk.get("overruled", "")).startswith("unproven cover t235"),
              "writer dropped the veto evidence (the RCA's own defect 2.2)")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_status_is_read_from_the_record_not_assumed():
    import session_review
    root = Path(tempfile.mkdtemp())
    try:
        tasks = root / "tasks"
        fpath = write_finding(root, finding_record())
        task_file(tasks, "t235", "queued")
        rec = session_review.write_rca_outcome(fpath, outcome_task="t235",
                                               rca_report=REPORT, tasks_dir=tasks)
        check(rec["outcome_status"] == "queued",
              "a queued owner task must report 'queued', got %r" % (rec.get("outcome_status"),))
        check(session_review.rca_outcome_resolved(fpath, tasks_dir=tasks) is False,
              "an RCA whose owner task is queued is NOT resolved")
        task_file(tasks, "t235", "done")
        rec2 = session_review.write_rca_outcome(fpath, outcome_task="t235",
                                                rca_report=REPORT, tasks_dir=tasks)
        check(rec2["outcome_status"] == "done",
              "status must be re-read from disk after the task closed, got %r" % (rec2.get("outcome_status"),))
        check(session_review.rca_outcome_resolved(fpath, tasks_dir=tasks) is True,
              "a finding whose owner task is done must be resolved -- that is the re-dispatch refusal")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_missing_owner_is_unknown_not_done():
    import session_review
    root = Path(tempfile.mkdtemp())
    try:
        tasks = root / "tasks"
        tasks.mkdir()
        fpath = write_finding(root, finding_record())
        rec = session_review.write_rca_outcome(fpath, outcome_task="t235",
                                               rca_report=REPORT, tasks_dir=tasks)
        check(rec["outcome_status"] == "unknown",
              "an owner task with no file on disk must report 'unknown', got %r" % (rec.get("outcome_status"),))
        check(session_review.rca_outcome_resolved(fpath, tasks_dir=tasks) is False,
              "'unknown' must not count as resolved")
        disk = json.loads(fpath.read_text(encoding="utf-8"))
        disk["outcome_status"] = "done"
        fpath.write_text(json.dumps(disk), encoding="utf-8")
        shutil.rmtree(root / "tasks")
        check(session_review.rca_outcome_resolved(fpath) is True,
              "a finding carrying outcome_status 'done' must refuse a re-dispatch even with no tasks dir")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_record_without_an_outcome_is_still_dispatchable():
    import session_review
    root = Path(tempfile.mkdtemp())
    try:
        tasks = root / "tasks"
        task_file(tasks, "t235", "done")
        fpath = write_finding(root, finding_record())
        check(session_review.rca_outcome_resolved(fpath, tasks_dir=tasks) is False,
              "a finding with no outcome field (today's on-disk shape) must NOT be refused")
        rec = session_review.write_rca_outcome(fpath, outcome_task="t235",
                                               rca_report=REPORT,
                                               completed_at="2026-10-08T07:59:21Z",
                                               tasks_dir=tasks)
        check(rec.get("disposition") == "deferred",
              "writer changed disposition when none was passed: %r" % (rec.get("disposition"),))
        check(rec.get("covered_by") == "",
              "writer invented a covered_by when none was passed: %r" % (rec.get("covered_by"),))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_existing_covered_by_is_never_blanked():
    import session_review
    root = Path(tempfile.mkdtemp())
    try:
        tasks = root / "tasks"
        task_file(tasks, "t235", "done")
        rec = finding_record()
        rec["covered_by"] = "t235"
        fpath = write_finding(root, rec)
        out = session_review.write_rca_outcome(fpath, outcome_task="t999",
                                               covered_by="t999", rca_report=REPORT,
                                               tasks_dir=tasks)
        check(out["covered_by"] == "t235",
              "an existing covered_by must be kept, not overwritten with a new guess: %r" % (out["covered_by"],))
        empty = finding_record()
        fpath2 = write_finding(root / "b", empty) if False else None
        (root / "b").mkdir()
        fpath2 = root / "b" / (KEY + ".json")
        fpath2.write_text(json.dumps(empty), encoding="utf-8")
        out2 = session_review.write_rca_outcome(fpath2, outcome_task="t235",
                                                covered_by="t235", rca_report=REPORT,
                                                tasks_dir=tasks)
        check(out2["covered_by"] == "t235",
              "an empty covered_by must be filled with the resolved owner: %r" % (out2["covered_by"],))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_writer_is_idempotent():
    import session_review
    root = Path(tempfile.mkdtemp())
    try:
        tasks = root / "tasks"
        task_file(tasks, "t235", "done")
        fpath = write_finding(root, finding_record())
        kw = dict(outcome_task="t235", rca_report=REPORT, disposition="covered",
                  completed_at="2026-10-08T07:59:21Z", tasks_dir=tasks)
        first = session_review.write_rca_outcome(fpath, **kw)
        second = session_review.write_rca_outcome(fpath, **kw)
        disk = json.loads(fpath.read_text(encoding="utf-8"))
        check(first == second, "second write changed the outcome: %r vs %r" % (first, second))
        check(disk == second, "returned dict is not what is on the file")
        check(sorted(disk) == sorted(first), "keys changed between runs")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_rca_calls_the_writer_on_completion():
    import session_review
    src = inspect.getsource(session_review.rca)
    check("write_rca_outcome(" in src,
          "rca() never calls write_rca_outcome: the outcome is computed and thrown away, "
          "which is the exact defect (RCA 028b97d00e2a 2.4: ledger row written, finding record untouched)")


if __name__ == "__main__":
    tests = [
        test_writer_records_the_outcome_and_reads_status_from_disk,
        test_status_is_read_from_the_record_not_assumed,
        test_missing_owner_is_unknown_not_done,
        test_record_without_an_outcome_is_still_dispatchable,
        test_existing_covered_by_is_never_blanked,
        test_writer_is_idempotent,
        test_rca_calls_the_writer_on_completion,
    ]
    for t in tests:
        try:
            t()
        except Exception as exc:
            FAILS.append("%s raised %s: %s" % (t.__name__, type(exc).__name__, exc))
            print("FAIL: %s raised %s: %s" % (t.__name__, type(exc).__name__, exc))
    print("%d/%d passed" % (len(tests) - len(FAILS), len(tests)))
    sys.exit(1 if FAILS else 0)
