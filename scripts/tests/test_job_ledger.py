"""t1059 -- a backgrounded job leaves a terminal row in the repo.

The endpoint that moves a long-running command to the background writes its evidence to
<proj>/<session>/tasks/<id>.output under a tmpfs path, and that file is gone days before the
session review runs (RCA 7cd688d83547, section 1: cat on the real bh9a1eges.output fails).
The review lane therefore judges an "I'll run X" promise with no access to the artefact that
discharges it. scripts/session_review.py:64 documents why the transcript has to be read by a
new reader: turns() keeps human/assistant text and DROPS tool traffic -- and the background
endpoint, and the run's pass/fail body, are tool traffic.

This suite pins the collector: read a session transcript, find every 'moved to the background
(ID: ...)' tool row, and append one machine row per job to a JSONL ledger, built from the
transcript ALONE -- so the row exists after the output file is deleted, and its counts do not
come from that file.

Red on main because session_review.py has none of these four functions today.
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import session_review as jobledger  # noqa: E402

SESSION_ID = "a15c2172-0900-44fc-b8eb-71e98a90aa6f"
TASK_ID = "bh9a1eges"

# the command that was backgrounded, long on purpose: a recorded value is never sliced
LONG_CMD = ("for i in 1 2 3 4 5 6 7 8 9 10; do timeout 1500 python3 scripts/run_tests.py "
            "-k test_coder_branch_delivery 2>&1 | grep -E 'pass |FAIL |flaky'; done "
            + "reproduce-7cd688d83547 " * 12)

# The shape of a run_tests.py sweep body (RCA 7cd688d83547, transcript line 3271): every run
# is echoed twice -- a bracketed '[1/2] pass ...' line AND a bare 'pass' line. Two DISTINCT
# suite names here, so a fixture that cannot tell them apart passes vacuously.
BODY = ("[1/2] pass test_coder_branch_delivery.py 356.2s\n"
        "pass\n"
        "[2/2] FAIL test_flaky_neighbour.py 339.8s\n"
        "FAIL\n"
        "[exited with code 0]\n")

# a body that must NEVER be read from disk: three fails, to catch a collector that opens the
# output file instead of reading the transcript's own copy of the output
DECOY_BODY = ("[1/3] FAIL a.py 1.0s\n[2/3] FAIL b.py 1.0s\n[3/3] FAIL c.py 1.0s\n"
              "[exited with code 1]\n")


def _bg_sentence(out_path):
    return ("Command did not complete within its 600s timeout and was moved to the "
            "background (ID: %s). Output is being written to: %s. You will be notified "
            "when it completes. If it is still running after 30m in the background, it "
            "will be stopped and you will be notified." % (TASK_ID, out_path))


def _session_lines(out_path):
    """Transcript rows in the repo's task-sessions shape (kind/at/round/tool/args/result)."""
    return [
        {"kind": "model", "at": "2026-10-02T12:08:16.921Z", "round": 217,
         "content": "I'll try to reproduce it rather than keep reasoning about it."},
        {"kind": "tool", "at": "2026-10-02T12:18:19.155Z", "round": 218,
         "tool": "run_command", "args": {"command": LONG_CMD},
         "result": _bg_sentence(out_path)},
        {"kind": "tool", "at": "2026-10-02T12:20:46.127Z", "round": 219,
         "tool": "run_command",
         "args": {"command": "cat %s" % out_path},
         "result": BODY},
    ]


def _fake_output_path(td, contents=None):
    """Mirror the real layout: <td>/claude-1000/<proj>/<session>/tasks/<id>.output"""
    p = Path(td) / "claude-1000" / "proj" / SESSION_ID / "tasks" / (TASK_ID + ".output")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(contents if contents is not None else BODY, encoding="utf-8")
    return p


def _write_session(td, rows):
    path = Path(td) / "session.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return path


def _ledger_rows(ledger):
    with open(ledger, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_finds_the_background_endpoint_and_its_output_path():
    out = "/fake/claude-1000/proj/%s/tasks/%s.output" % (SESSION_ID, TASK_ID)
    jobs = jobledger.find_backgrounded(_bg_sentence(out))
    assert len(jobs) == 1, "expected one job, got %r" % (jobs,)
    assert jobs[0]["task_id"] == TASK_ID, jobs[0]
    assert jobs[0]["output_path"] == out, jobs[0]


def test_ignores_text_that_is_not_the_background_endpoint():
    assert jobledger.find_backgrounded(LONG_CMD) == []
    assert jobledger.find_backgrounded(BODY) == []


def test_counts_pass_and_fail_from_the_bracketed_form_only():
    # the double-listing rule: a bare 'pass'/'FAIL' line is the echo of the bracketed line, so
    # counting bare lines double-counts every run. (RCA 7cd688d83547: 'n_pass must be counted
    # from the bracketed form')
    assert jobledger.count_from_output(BODY) == (1, 1), jobledger.count_from_output(BODY)
    assert jobledger.count_from_output("pass\nFAIL\n") == (0, 0)
    assert jobledger.count_from_output("") == (0, 0)


def test_exit_code_is_read_from_the_collection_line():
    assert jobledger.find_exit_code(BODY) == 0, jobledger.find_exit_code(BODY)
    assert jobledger.find_exit_code("[exited with code 137]") == 137
    assert jobledger.find_exit_code("no exit line here") is None


def test_terminal_row_survives_the_deleted_output_file():
    with tempfile.TemporaryDirectory() as td:
        out = _fake_output_path(td)
        session = _write_session(td, _session_lines(str(out)))
        ledger = Path(td) / "job-ledger.jsonl"
        out.unlink()                      # the RCA's fixture: the output file is already gone
        n = jobledger.collect_job_rows(str(session), str(ledger))
        assert n == 1, "expected one row written, got %r" % (n,)
        rows = _ledger_rows(ledger)
        assert len(rows) == 1, rows
        row = rows[0]
        assert set(row) == {"session", "task_id", "command", "launch_ts", "output_path",
                            "exit_code", "n_pass", "n_fail", "collected_at",
                            "collected_by_turn"}, sorted(row)
        assert row["task_id"] == TASK_ID, row
        assert row["session"] == SESSION_ID, row  # derived from .../<session>/tasks/ in the path
        assert row["command"] == LONG_CMD, "the command must be recorded whole, not sliced"
        assert row["launch_ts"] == "2026-10-02T12:18:19.155Z", row
        assert row["collected_at"] == "2026-10-02T12:20:46.127Z", row
        assert row["collected_by_turn"] == 219, row
        assert row["exit_code"] == 0, row
        assert row["n_pass"] == 1, row
        assert row["n_fail"] == 1, row
        assert row["output_path"] == str(out), row


def test_counts_come_from_the_transcript_not_from_the_output_file():
    with tempfile.TemporaryDirectory() as td:
        out = _fake_output_path(td, contents=DECOY_BODY)   # 3 FAILs, present on disk
        session = _write_session(td, _session_lines(str(out)))
        ledger = Path(td) / "job-ledger.jsonl"
        jobledger.collect_job_rows(str(session), str(ledger))
        row = _ledger_rows(ledger)[0]
        assert (row["n_pass"], row["n_fail"]) == (1, 1), (
            "counts came from the /tmp file, which ages out: %r" % (row,))
        assert row["exit_code"] == 0, row


def test_a_job_with_no_collection_row_records_the_unknowns_as_none():
    with tempfile.TemporaryDirectory() as td:
        out = _fake_output_path(td)
        rows = _session_lines(str(out))[:2] + [
            {"kind": "model", "at": "2026-10-02T12:21:38.036Z", "round": 220,
             "content": "the sweep is still running, moving on."}]
        session = _write_session(td, rows)
        ledger = Path(td) / "job-ledger.jsonl"
        assert jobledger.collect_job_rows(str(session), str(ledger)) == 1
        row = _ledger_rows(ledger)[0]
        assert set(row) == {"session", "task_id", "command", "launch_ts", "output_path",
                            "exit_code", "n_pass", "n_fail", "collected_at",
                            "collected_by_turn"}, sorted(row)
        assert row["exit_code"] is None, row
        assert row["n_pass"] is None, row
        assert row["n_fail"] is None, row
        assert row["launch_ts"] == "2026-10-02T12:18:19.155Z", row


def test_collect_is_idempotent_for_a_job_already_ledgered():
    with tempfile.TemporaryDirectory() as td:
        out = _fake_output_path(td)
        session = _write_session(td, _session_lines(str(out)))
        ledger = Path(td) / "job-ledger.jsonl"
        assert jobledger.collect_job_rows(str(session), str(ledger)) == 1
        assert jobledger.collect_job_rows(str(session), str(ledger)) == 0
        assert len(_ledger_rows(ledger)) == 1, "the second pass must not duplicate the row"


def test_ledger_is_appended_not_rewritten():
    with tempfile.TemporaryDirectory() as td:
        out = _fake_output_path(td)
        session = _write_session(td, _session_lines(str(out)))
        ledger = Path(td) / "job-ledger.jsonl"
        ledger.write_text(json.dumps({"session": "other", "task_id": "zzz"}) + "\n",
                          encoding="utf-8")
        jobledger.collect_job_rows(str(session), str(ledger))
        rows = _ledger_rows(ledger)
        assert len(rows) == 2, rows
        assert rows[0] == {"session": "other", "task_id": "zzz"}, "earlier rows must survive"
        assert rows[1]["task_id"] == TASK_ID, rows[1]


def test_a_session_with_no_backgrounded_job_writes_nothing():
    with tempfile.TemporaryDirectory() as td:
        rows = [{"kind": "tool", "at": "2026-10-02T12:00:00Z", "round": 1,
                 "tool": "run_command", "args": {"command": LONG_CMD},
                 "result": "1 passed in 356.2s"}]
        session = _write_session(td, rows)
        ledger = Path(td) / "job-ledger.jsonl"
        assert jobledger.collect_job_rows(str(session), str(ledger)) == 0
        assert not ledger.exists(), "no backgrounded job, no row, no file"


def _main():
    tests = [(name, obj) for name, obj in sorted(globals().items())
             if name.startswith("test_") and callable(obj)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("ok    %s" % name)
        except Exception as exc:
            failed += 1
            print("FAILED %s: %s: %s" % (name, type(exc).__name__, exc))
    print("%d test(s), %d failure(s)" % (len(tests), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_main())
