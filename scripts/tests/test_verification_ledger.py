#!/usr/bin/env python3
r"""t1011 -- every promised check leaves a row on disk, so a discharge can be evidenced.

Red on main for ONE reason, the missing change: scripts/session_review.py defines none of
VERIFICATION_LEDGER, record_verification, verification_rows, can_discharge. Nothing today
records that a promised check ran, which is what workspace/rca/1190f930bdb9.md proves the
cost of -- the session promised "I'll check first and I'll tell you", the check provably
never ran, and the only evidence of that is a prose transcript nobody re-reads.
workspace/rca/1a7761b3791e.md rows 3506/3519 are the mirror case: a verification recorded as
PASSING over a mutation that never happened, detectable only if the row pins the exit code,
the digest of the bytes the command actually produced, and the digest of the artifact as it
stood after the check.

This suite lands in scripts/session_review.py, the file the RCA's CHANGE can be built into,
because coder.py can only edit a file that exists -- the same resolution t808 used after the
identical refusal. Every test below passes a temp ledger, so live workspace state is never
written.

Run: python3 scripts/tests/test_verification_ledger.py  (pytest is not installed)
"""
import hashlib
import importlib.util
import json
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "session_review.py"

NEW = ("VERIFICATION_LEDGER", "record_verification", "verification_rows", "can_discharge")
FIELDS = ("finding_key", "verify_command", "ran_at", "exit", "artifact", "sha256_of_output")
KEY = "1190f930bdb9"
OTHER_KEY = "1a7761b3791e"
ARTIFACT = "workspace/tasks/t1011.md"


def _py(body):
    """One python3 -c command, built here so no quoting is duplicated below."""
    return "python3 -c '" + body + "'"


# Every command produces a DISTINCT output on purpose (t471/t297/t370: one repeated
# placeholder cannot tell the real constants apart). Every expected digest below is computed
# independently in the test, never by calling into the module.
CMD_A = _py('print("alpha-out")')
CMD_B = _py('print("beta-out")')
CMD_FAIL = _py('import sys; sys.stdout.write("boom"); sys.exit(3)')
CMD_BOTH = _py('import sys; sys.stdout.write("out-part"); sys.stderr.write("err-part")')
CMD_LONG = _py('print("z" * 5000)')
CMD_SLOW = _py('import time; time.sleep(5)')

ABSENT = ("no writer exists at the verification endpoint, so 'the promised check ran' is "
          "unprovable in both directions -- absent is invisible (rca 1190f930bdb9) and a "
          "fake pass is invisible (rca 1a7761b3791e, row 3506)")


def _load():
    spec = importlib.util.spec_from_file_location("session_review", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mod():
    mod = _load()
    missing = [n for n in NEW if not hasattr(mod, n)]
    assert not missing, (
        "scripts/session_review.py exists but defines none of: %s -- %s"
        % (", ".join(missing), ABSENT))
    return mod


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rows_of(ledger):
    if not ledger.is_file():
        return []
    return [json.loads(line) for line in
            ledger.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_writer_names_exist():
    """Red today for the stated reason: not one of the four names is in the file."""
    _mod()


def test_the_ledger_is_the_file_the_change_names():
    mod = _mod()
    p = Path(mod.VERIFICATION_LEDGER)
    assert p.name == "verification-ledger.jsonl", (
        "the ledger's file name is part of the contract (GROUP: file:"
        "workspace/verification-ledger.jsonl), got %r" % (p.name,))
    assert p.parent.name == "workspace", (
        "the ledger lives at repo-root/workspace, not inside scripts/, got %r" % (p,))


def test_recording_a_check_appends_a_row_carrying_every_field():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "verification-ledger.jsonl"
        row = mod.record_verification(finding_key=KEY, verify_command=CMD_A,
                                      artifact=ARTIFACT,
                                      out={"exit": 0, "stdout": "alpha-out\n", "stderr": ""},
                                      ledger=ledger)
        written = _rows_of(ledger)
        assert len(written) == 1, (
            "record_verification must append exactly one row to the ledger it is given, "
            "got %d" % len(written))
        got = written[0]
        missing = [f for f in FIELDS if f not in got]
        assert not missing, (
            "the row is missing %r; all six are what makes a verification checkable by a "
            "later session -- the row on disk carries: %r" % (missing, sorted(got)))
        assert got["finding_key"] == KEY, (
            "a row that cannot name the finding it verifies cannot discharge that finding, "
            "got %r" % (got["finding_key"],))
        assert got["verify_command"] == CMD_A, (
            "the command is what a later session re-runs to re-check the claim, got %r"
            % (got["verify_command"],))
        assert got["artifact"] == ARTIFACT, "got %r" % (got["artifact"],)
        assert int(got["exit"]) == 0, (
            "a check that exited 0 must be recorded exit=0, got %r" % (got["exit"],))
        assert got["sha256_of_output"] == _digest("alpha-out\n"), (
            "the digest must be of the output that was OBSERVED -- expected the digest of "
            "stdout 'alpha-out' plus its trailing newline, got %r"
            % (got["sha256_of_output"],))
        datetime.strptime(got["ran_at"], "%Y-%m-%dT%H:%M:%SZ")
        assert (isinstance(row, dict)
                and row.get("sha256_of_output") == got["sha256_of_output"]
                and row.get("verify_command") == CMD_A), (
            "record_verification must return the row it wrote, so the caller can read what "
            "was observed instead of re-running the check")


def test_a_real_command_is_run_and_its_own_output_is_the_digest():
    """The default path -- no out=, so the module must run the command and digest what it
    captured. A build that fills sha256_of_output from a constant passes the field-shape
    test above and dies here (check-satisfied-by-dead-path)."""
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "verification-ledger.jsonl"
        row = mod.record_verification(finding_key=KEY, verify_command=CMD_A,
                                      out=None, timeout=60, ledger=ledger)
        assert int(row["exit"]) == 0, "got %r" % (row["exit"],)
        assert row["sha256_of_output"] == _digest("alpha-out\n"), (
            "the recorded digest must be of the bytes the command printed, got %r"
            % (row["sha256_of_output"],))
        assert _rows_of(ledger)[0]["sha256_of_output"] == row["sha256_of_output"], (
            "the row on disk must be the row returned")


def test_the_digest_covers_stdout_and_stderr_together():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "verification-ledger.jsonl"
        row = mod.record_verification(finding_key=KEY, verify_command=CMD_BOTH,
                                      out={"exit": 1, "stdout": "out-part",
                                           "stderr": "err-part"},
                                      ledger=ledger)
        assert row["sha256_of_output"] == _digest("out-part" + "err-part"), (
            "the recorded output is stdout then stderr, whole -- a digest of one stream hides "
            "the diagnostic half of a failure, got %r" % (row["sha256_of_output"],))


def test_a_check_that_fails_is_recorded_as_failing_not_as_a_pass():
    """The row-3506 inverse: a ledger that records only successes can certify a mutation
    that never happened."""
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "verification-ledger.jsonl"
        row = mod.record_verification(finding_key=KEY, verify_command=CMD_FAIL,
                                      out=None, timeout=60, ledger=ledger)
        assert int(row["exit"]) == 3, (
            "a check that exited 3 must be ledgered as exit 3, got %r" % (row["exit"],))
        assert row["sha256_of_output"] == _digest("boom"), (
            "the output a failing check produced is still the record, got %r"
            % (row["sha256_of_output"],))


def test_a_long_output_is_digest_whole_not_cut():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "verification-ledger.jsonl"
        row = mod.record_verification(finding_key=KEY, verify_command=CMD_LONG,
                                      out=None, timeout=60, ledger=ledger)
        expected = _digest("z" * 5000 + "\n")
        assert row["sha256_of_output"] == expected, (
            "a recorded value is never truncated: the digest covers all 5000 characters plus "
            "the newline, expected %s got %r" % (expected, row["sha256_of_output"]))


def test_two_checks_with_different_output_cannot_share_one_record():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "verification-ledger.jsonl"
        a = mod.record_verification(finding_key=KEY, verify_command=CMD_A,
                                    out={"exit": 0, "stdout": "alpha-out\n", "stderr": ""},
                                    ledger=ledger)
        b = mod.record_verification(finding_key=KEY, verify_command=CMD_B,
                                    out={"exit": 0, "stdout": "beta-out\n", "stderr": ""},
                                    ledger=ledger)
        assert a["sha256_of_output"] != b["sha256_of_output"], (
            "two checks whose output differs must not digest alike -- that is what makes a "
            "verification of a no-op mutation detectable")
        rows = _rows_of(ledger)
        assert len(rows) == 2, (
            "the ledger appends: two checks must leave two rows, got %d" % len(rows))
        assert [r["verify_command"] for r in rows] == [CMD_A, CMD_B], (
            "rows must land in the order the checks ran, in append mode")


def test_the_artifact_digest_records_the_file_as_it_stood_after_the_check():
    """rca 1a7761b3791e row 3506: '0 warnings (correct)' recorded over a mutation that never
    happened. The row carries the sha256 of the target as it stood when the check ran, so a
    later session can tell the verification apart from the file it claims to prove."""
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "verification-ledger.jsonl"
        target = Path(td) / "target.py"
        target.write_text("alpha\n", encoding="utf-8")
        row = mod.record_verification(finding_key=KEY, verify_command=CMD_A,
                                      artifact=str(target),
                                      out={"exit": 0, "stdout": "alpha-out\n", "stderr": ""},
                                      target=str(target), ledger=ledger)
        assert row.get("sha256_of_artifact") == hashlib.sha256(b"alpha\n").hexdigest(), (
            "the row must carry the digest of the artifact's bytes AFTER the check ran, got "
            "%r" % (row.get("sha256_of_artifact"),))
        target.write_text("beta\n", encoding="utf-8")   # the mutation claimed and not made
        on_disk = _rows_of(ledger)[0]
        assert on_disk["sha256_of_artifact"] == hashlib.sha256(b"alpha\n").hexdigest(), (
            "reading the ledger must not rewrite history: the row keeps the digest of what "
            "was there when the check ran, so the drift stays visible, got %r"
            % (on_disk.get("sha256_of_artifact"),))
        assert on_disk["sha256_of_artifact"] != hashlib.sha256(b"beta\n").hexdigest(), (
            "the recorded digest must not equal the digest of bytes written after the check "
            "-- that inequality is the only mechanical way to see row 3506")


def test_a_row_for_one_finding_does_not_discharge_another():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "verification-ledger.jsonl"
        assert not mod.can_discharge(finding_key=KEY, ledger=ledger), (
            "can_discharge must be False when no row exists: a deferral closed with no "
            "evidence is indistinguishable from one that was never discharged (t025)")
        assert list(mod.verification_rows(ledger=ledger)) == [], (
            "verification_rows must return [] for a ledger that does not exist, not raise")
        mod.record_verification(finding_key=KEY, verify_command=CMD_A,
                                out={"exit": 0, "stdout": "alpha-out\n", "stderr": ""},
                                ledger=ledger)
        assert mod.can_discharge(finding_key=KEY, ledger=ledger), (
            "can_discharge must be True once a row exists for that finding key")
        assert not mod.can_discharge(finding_key=OTHER_KEY, ledger=ledger), (
            "a row recorded against one finding key must not stand as evidence that a "
            "different finding's promised check ran")


def test_a_check_that_overruns_its_timeout_still_leaves_a_row():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "verification-ledger.jsonl"
        row = mod.record_verification(finding_key=KEY, verify_command=CMD_SLOW,
                                      out=None, timeout=1, ledger=ledger)
        assert int(row["exit"]) == 124, (
            "a check killed by its own timeout is recorded exit=124 -- exit 0 is the row-3506 "
            "lie, and no exit at all is t808's exit-143 case: a verification indistinguishable "
            "from one that never ran. got %r" % (row["exit"],))
        assert len(_rows_of(ledger)) == 1, (
            "the timeout handler RECORDS what it caught into the row; it must not swallow "
            "the exception and continue with no row")


def test_recording_a_check_never_writes_the_live_ledger_from_a_test():
    mod = _mod()
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "verification-ledger.jsonl"
        mod.record_verification(finding_key=KEY, verify_command=CMD_A,
                                out={"exit": 0, "stdout": "alpha-out\n", "stderr": ""},
                                ledger=ledger)
        assert not Path(mod.VERIFICATION_LEDGER).exists(), (
            "an explicit ledger must not create the live ledger at %r -- importing this "
            "module and recording to a temp path must leave workspace/ untouched"
            % (mod.VERIFICATION_LEDGER,))


TESTS = [(name, obj) for name, obj in sorted(globals().items())
         if name.startswith("test_") and callable(obj)]

if __name__ == "__main__":
    failed = 0
    for name, fn in TESTS:
        try:
            fn()
            print("ok   %s" % name)
        except Exception:
            failed += 1
            print("FAIL %s" % name)
            traceback.print_exc()
    print("%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    raise SystemExit(1 if failed else 0)
