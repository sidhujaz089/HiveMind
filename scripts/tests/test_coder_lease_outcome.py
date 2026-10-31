"""A coder.py dispatch refused by its twin guard must record its own outcome.

WHY THIS TEST EXISTS. On 2026-10-13 a dispatch of brief-204cb150 on model swift
was refused by coder.py's concurrency guard (projects/local-inference/coder.py
line 2490) because pid 770443 already ran the same job. The model was never
invoked, yet the trail recorded the refusal as a model attempt and added swift
to the list of models that have failed this brief. A refusal that records
nothing is indistinguishable from a coding failure, so the model is charged for
a run it was never given.

THE SHAPE THIS COPIES. coder.py already records every other refusal it makes:
REFUSED-TO-START at line 2657, NEEDS-SCOPE at lines 2697 and 2729 -- each calls
record() with the outcome as a named string and returns 2. The twin branch was
the only refusal in the file that raised SystemExit with a prose message and
recorded nothing, which is why downstream it read as a coding failure.

NOTHING LIVE IS TOUCHED. Every module name this test depends on is replaced
before main() runs: running_twin reports a foreign pid, record is a fake that
appends to a list, sandbox and ask raise AssertionError if they are reached.
No file under workspace/ is written, no model is asked for a token, no child
process is started.
"""
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CODER = ROOT / "projects" / "local-inference" / "coder.py"
MODEL = "fake-model-never-called"
TWIN_PID = 770443
OUTCOME = "LEASE-HELD"
BRIEF = ("LEASE REFUSAL PROBE\n"
         "A dispatch that must be refused because a twin already holds this job.\n")


def _load_coder():
    """Import coder.py by path, under its own name, so its __main__ block does not run."""
    spec = importlib.util.spec_from_file_location("coder_under_test", CODER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _refuse(mod, twin_pid):
    """Run coder.py's main() against a twin that holds the lease.

    Returns (exit_code, printed_text, rows). rows is everything coder.py handed
    to record() -- a ledger row is the only account of a dispatch that a later
    reader can check, and today this dispatch leaves none.
    """
    rows = []

    def fake_record(task, model, outcome, tries, patch, detail, target=None):
        rows.append({"task": task, "model": model, "outcome": outcome,
                     "tries": tries, "detail": detail, "target": target})

    def _never(name):
        def f(*args, **kwargs):
            raise AssertionError("%s was reached; a refused dispatch does no work" % name)
        return f

    mod.running_twin = lambda label, files, model: twin_pid
    mod.record = fake_record
    mod.sandbox = _never("sandbox()")
    if hasattr(mod, "ask"):
        mod.ask = _never("ask()")

    buf = io.StringIO()
    code = None
    try:
        with redirect_stdout(buf):
            returned = mod.main(["--brief", BRIEF, "--model", MODEL, "--attempts", "1"])
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
    else:
        code = returned
    return code, buf.getvalue(), rows


def test_twin_refusal_records_a_row():
    """A refusal that records nothing is invisible to every later reader."""
    mod = _load_coder()
    code, out, rows = _refuse(mod, TWIN_PID)
    assert rows, ("a dispatch refused by the twin guard recorded no ledger row, so "
                  "the failure trail charges the model with a run it never got: %r"
                  % out[:400])


def test_the_recorded_outcome_is_a_named_refusal_not_a_model_attempt():
    mod = _load_coder()
    code, out, rows = _refuse(mod, TWIN_PID)
    if not rows:
        raise AssertionError("nothing was recorded -- outcome cannot be checked")
    assert rows[0]["outcome"] == OUTCOME, (
        "outcome %r is not %r: a lease refusal must carry its own class, not the "
        "outcome of an attempt the model was never allowed to make"
        % (rows[0]["outcome"], OUTCOME))


def test_the_recorded_row_names_the_model_that_was_never_invoked():
    mod = _load_coder()
    code, out, rows = _refuse(mod, TWIN_PID)
    if not rows:
        raise AssertionError("nothing was recorded -- model cannot be checked")
    assert rows[0]["model"] == MODEL, (
        "record() was given model %r, not %r: the row must say which model was "
        "denied its attempt, which is the fact the failed-models list reads"
        % (rows[0]["model"], MODEL))


def test_the_recorded_detail_names_the_pid_that_holds_the_lease():
    mod = _load_coder()
    code, out, rows = _refuse(mod, TWIN_PID)
    if not rows:
        raise AssertionError("nothing was recorded -- detail cannot be checked")
    detail = str(rows[0]["detail"] or "")
    assert str(TWIN_PID) in detail, (
        "the recorded detail %r does not name the pid that holds the lease; "
        "without it a reader cannot tell a live twin from a stale one" % detail[:300])
    assert "already running" in detail, (
        "the recorded detail %r dropped the reason: already running" % detail[:300])
    assert MODEL in detail, (
        "the recorded detail %r does not name the model denied the attempt" % detail[:300])


def test_refusal_exits_with_the_refusal_code():
    mod = _load_coder()
    code, out, rows = _refuse(mod, TWIN_PID)
    assert code == 2, (
        "a twin refusal exited %r; every other refusal in coder.py exits 2 (see "
        "the REFUSED-TO-START and NEEDS-SCOPE paths), and an int exit code is the "
        "only cause a later reader can machine-check" % (code,))


def test_the_human_still_sees_the_reason_including_the_recovery_line():
    mod = _load_coder()
    code, out, rows = _refuse(mod, TWIN_PID)
    assert "is already running this job" in out, (
        "the refusal no longer says what it says today; the text a human reads is "
        "the only account of a refused dispatch: %r" % out[:400])
    assert "kill it first" in out, (
        "the refusal no longer carries its recovery line 'Wait for it, or kill it "
        "first': %r" % out[:400])


TESTS = [
    test_twin_refusal_records_a_row,
    test_the_recorded_outcome_is_a_named_refusal_not_a_model_attempt,
    test_the_recorded_row_names_the_model_that_was_never_invoked,
    test_the_recorded_detail_names_the_pid_that_holds_the_lease,
    test_refusal_exits_with_the_refusal_code,
    test_the_human_still_sees_the_reason_including_the_recovery_line,
]

if __name__ == "__main__":
    fails = []
    for fn in TESTS:
        try:
            fn()
        except Exception as e:                                    # noqa: BLE001
            fails.append((fn.__name__, e))
            print("FAIL %s\n%s" % (fn.__name__, e))
    print("%d/%d passed" % (len(TESTS) - len(fails), len(TESTS)))
    sys.exit(1 if fails else 0)
