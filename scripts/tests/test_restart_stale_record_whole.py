#!/usr/bin/env python3
"""t1187 -- the deploy hook's record of a FAILED restart must carry the WHOLE reason.

scripts/restart_stale.py is the mechanism t1187 deploys: it restarts the services a change made
stale and appends one row to workspace/restart-stale.log saying what it did. Today, when a
restart fails, that row says "RESTART FAILED: " plus the runner's stderr SLICED TO 120
CHARACTERS (scripts/restart_stale.py:171), and `acted` is written to the log at lines 199-201.
A real systemd failure puts the cause past character 120, so the permanent record of the deploy
hook's one failure mode is a message with the reason cut off -- exactly the defect
docs/skills/never-truncate-a-record exists for: a truncated record reads as a complete one.

RED ON MAIN for the stated reason: restart_stale.py defines no function that builds that record
whole, so every case below dies at the attribute lookup (and the source case fails on the slice).

NOTHING LIVE IS TOUCHED: no systemctl, no /proc, no subprocess, no model, no file written. The
oracle calls the function with a fake completed-process object (bytes stdout/stderr, exactly the
shape subprocess.run returns at line 169, which is called WITHOUT text=True), and reads the
module's own source to prove the call site uses the function and slices nothing.
"""
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
SCRIPT = ROOT / "scripts" / "restart_stale.py"

_spec = importlib.util.spec_from_file_location("restart_stale_under_test", SCRIPT)
rs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rs)                         # module top level is imports + constants only

FAILURES = []

# The shape a systemd restart failure actually arrives in: a cause sentence that begins after
# the boilerplate, i.e. PAST the 120-character cut the record takes today.
CAUSE_TAIL = ("the unit is masked by a symlink to /dev/null, so a restart is refused; "
              "run 'systemctl unmask memory-guard.service' and try again")
STDERR = ("Job for memory-guard.service failed because the control process exited with error "
          "code.\nSee 'systemctl status memory-guard.service' and 'journalctl -xe' for details.\n"
          + "x" * 200 + "\n" + CAUSE_TAIL)


class FakeRun:
    """Stands in for the CompletedProcess subprocess.run returns at restart_stale.py:169."""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def check(cond, msg):
    if not cond:
        FAILURES.append(msg)
        print("FAIL: " + msg)


def read_source():
    return SCRIPT.read_text(encoding="utf-8")


def test_failure_record_carries_the_whole_reason():
    """The one assertion that matters: nothing of the runner's stderr is thrown away."""
    fn = rs.restart_failure_detail
    out = fn(FakeRun(1, b"", STDERR.encode("utf-8")))
    check(isinstance(out, str), f"the failure record must be a string, got {type(out).__name__}")
    check(out == "RESTART FAILED: " + STDERR,
          "the failure record must be 'RESTART FAILED: ' + the WHOLE decoded stderr; "
          f"got {out!r}")
    # Spelled out separately so a failure names the part that was cut, not just the whole.
    check(CAUSE_TAIL in out,
          f"the record must contain the cause at the END of the stderr (past the old 120-char "
          f"cut), which is the part a slice destroys; got {out!r}")


def test_failure_record_names_the_unit_that_failed():
    """A record must say WHAT failed, in words a person can act on -- not a code."""
    out = rs.restart_failure_detail(FakeRun(1, b"", STDERR.encode("utf-8")))
    check("memory-guard.service" in out,
          f"the record must carry the unit name from the runner's stderr; got {out!r}")


def test_a_failure_with_no_stderr_is_still_a_record():
    """A header with nothing under it: the failure word must survive an empty stderr, and a
    None stderr (subprocess.run can hand one back) must not raise."""
    for stderr in (b"", None):
        out = rs.restart_failure_detail(FakeRun(1, b"", stderr))
        check(isinstance(out, str) and out.startswith("RESTART FAILED"),
              f"returncode 1 with stderr={stderr!r} must record a failure that says so; got {out!r}")
        check(len(out) > len("RESTART FAILED"),
              f"a failure record must say more than the word FAILED; got {out!r}")


def test_a_successful_restart_is_recorded_by_the_existing_word():
    """Pins the success string already in the file (restart_stale.py:170), so the fix cannot
    rename what the log has always said."""
    out = rs.restart_failure_detail(FakeRun(0))
    check(out == "restarted", f"a returncode 0 must record exactly 'restarted'; got {out!r}")


def test_the_call_site_uses_it_and_slices_nothing():
    """A function nobody calls is a decoration. The record built in main() must call it, and no
    slice may survive anywhere the log row is assembled."""
    src = read_source()
    check("def restart_failure_detail(" in src,
          "restart_stale.py must define restart_failure_detail (the whole-record builder)")
    check("[:120]" not in src,
          "the 120-character slice on the failure record must be gone from the file "
          "(docs/skills/never-truncate-a-record: a record is never cut)")
    lines = src.splitlines()
    slice_re = re.compile(r"\[\s*:\s*\d+\]")
    for i, line in enumerate(lines):
        if "acted.append(" in line:
            window = "\n".join(lines[i:i + 3])          # the statement's continuation lines
            hit = slice_re.search(window)
            check(hit is None,
                  f"the log row built at line {i + 1} must not slice a recorded value "
                  f"(found {hit.group(0) if hit else ''!r} in {window!r})")


def main():
    tests = sorted((v for k, v in globals().items()
                    if k.startswith("test_") and callable(v)), key=lambda f: f.__name__)
    for fn in tests:
        try:
            fn()
        except Exception as e:                                              # noqa: BLE001
            FAILURES.append(f"{fn.__name__} raised {type(e).__name__}: {e}")
            print(f"FAIL: {fn.__name__} raised {type(e).__name__}: {e}")
    print(f"\n{len(tests)} test(s) run, {len(FAILURES)} failure(s)")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
