#!/usr/bin/env python3
r"""t808 -- a verification claim whose command did not complete within a measured
budget must be REFUSED, and must never be recorded as a green.

The specimen (workspace/tasks/t808--refuse-a-verification-claim-whose-command-did-no.md,
quoting the harness notification verbatim): the command

    timeout 280 python3 scripts/dispatch_loop.py --dry-run 2>&1 | grep -iE "senior|preflight\(s\)|PRIORITY|inference path|running," | head -10

failed with exit code 143 = 128 + SIGTERM. Why 280s was never a budget that command can
fit, and why that grep can never see a partial run, are the reads recorded in the task's
own checklist: the dry-run path runs two 120s collection subprocesses with no dry_run
guard (dispatch_loop.py:2207-2217) plus a probe whose worst case that file documents at
600s (:2255-2260), so 240s+ is consumable before any scheduling decision is printed; and
every token that grep selects is emitted at the END of tick(), while the dry-run return
sits above write_tick_row, so a dry run -- completed, timed out or killed -- writes no row
at all. Exit 143 with empty stdout is therefore representationally identical to a test
that was never run, and nothing in today's vocabulary can say which of the two it was.

This suite pins that vocabulary. It lands in scripts/session_review.py, the file the
RCA's CHANGE names, because coder.py can only edit a file that exists (the refusal on the
previous scope). It is pure: no I/O, no model call, no subprocess, nothing read from
workspace state -- every fixture below is a literal list.

Run: python3 scripts/tests/test_session_review_verification_claims.py  (pytest is not installed)
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "session_review.py"

# The names this task adds. Checked for existence first, so a build that adds four of
# six is reported as a named gap rather than crashing a later test.
VOCAB = ("REQUIRED_FIELDS", "VERDICT_VERIFIED", "VERDICT_UNVERIFIED",
         "VERDICT_REFUSED", "measured_worst_case", "evaluate")

# Verbatim from the finding (entities decoded). Kept as the key the module looks
# measurements up by, so command-string matching is itself under test.
CMD = (r'timeout 280 python3 scripts/dispatch_loop.py --dry-run 2>&1 '
       r'| grep -iE "senior|preflight\(s\)|PRIORITY|inference path|running," '
       r'| head -10')
OTHER = 'python3 scripts/ollama_events.py --since 2026-10-07'

# 610.0 = the documented worst case of the collectors plus the probe: 2 x 120s collectors
# (dispatch_loop.py:2207-2217) and a probe documented at 600s (:2255-2260). A real number,
# not a placeholder: the budget tests compare 280, 700 and 1800 against it.
MEASURED = 610.0
MEASUREMENTS = [{"command": CMD, "seconds": MEASURED}]

# The grep tokens from the finding, all printed at the end of tick(). Distinct strings on
# purpose: one repeated placeholder cannot tell the success predicate's inputs apart.
SUCCESS_TOKENS = ["inference path: up", "PRIORITY", "running,", "queued",
                  "--dry-run: nothing started, interrupted or recorded."]


def _load():
    if not MODULE_PATH.is_file():
        return None
    spec = importlib.util.spec_from_file_location("session_review", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _vocab():
    mod = _load()
    assert mod is not None, "no module at %s" % MODULE_PATH
    missing = [n for n in VOCAB if not hasattr(mod, n)]
    assert not missing, (
        "scripts/session_review.py defines none of the verification vocabulary this "
        "task adds: " + ", ".join(missing) + ". Nothing today can state that a "
        "verification command FINISHED, within what budget, with what exit code, over "
        "what population (workspace/tasks/t808--refuse-a-verification-claim-whose-command-did-no.md, THE FIX)")
    return mod


def _claim(**over):
    base = {
        "command": CMD,
        "budget_s": 700,          # >= 610 measured, <= 2*610: an admissible budget
        "exit_code": 143,         # the specimen's exit
        "completed": False,       # a SIGTERM'd run did not complete
        "budget_source": "measured",
        "population": "1 dry-run tick of dispatch_loop.py; 0 ledger rows written",
        "success_tokens": list(SUCCESS_TOKENS),
        "observed_tokens": [],    # the killed run matched none of them
    }
    base.update(over)
    return base


def _joined(out):
    return " | ".join(out["reasons"])


def test_the_vocabulary_names_exist():
    return _vocab()


def test_verdict_vocabulary_is_three_distinct_values():
    mod = _vocab()
    trio = (mod.VERDICT_VERIFIED, mod.VERDICT_UNVERIFIED, mod.VERDICT_REFUSED)
    assert len(set(trio)) == 3, trio
    assert mod.VERDICT_VERIFIED == "VERIFIED", trio
    assert mod.VERDICT_UNVERIFIED == "UNVERIFIED", trio
    assert mod.VERDICT_REFUSED == "REFUSED", trio
    assert set(mod.REQUIRED_FIELDS) == {"command", "budget_s", "exit_code", "completed",
                                        "budget_source", "population"}, mod.REQUIRED_FIELDS


def test_killed_run_is_unverified_and_names_the_exit_code():
    # The finding itself: exit 143, nothing completed, no token matched.
    mod = _vocab()
    out = mod.evaluate(_claim(), MEASUREMENTS)
    reasons = _joined(out)
    assert out["verdict"] == mod.VERDICT_UNVERIFIED, (out, reasons)
    assert "143" in reasons, reasons
    assert out["verdict"] != mod.VERDICT_VERIFIED


def test_exit_zero_with_no_named_token_is_unverified():
    # Exit 0 with empty output is the artefact this finding was walked past: a truncated
    # run must not satisfy the success predicate.
    mod = _vocab()
    out = mod.evaluate(_claim(exit_code=0, completed=True, observed_tokens=[]), MEASUREMENTS)
    reasons = _joined(out)
    assert out["verdict"] == mod.VERDICT_UNVERIFIED, (out, reasons)
    assert "token" in reasons.lower(), reasons


def test_incomplete_run_is_unverified_even_with_output():
    mod = _vocab()
    out = mod.evaluate(_claim(exit_code=0, completed=False,
                              observed_tokens=["inference path: up"]), MEASUREMENTS)
    reasons = _joined(out)
    assert out["verdict"] == mod.VERDICT_UNVERIFIED, (out, reasons)
    assert "completed" in reasons, reasons


def test_missing_fields_are_named_one_by_one():
    mod = _vocab()
    claim = _claim(exit_code=0, completed=True, observed_tokens=["inference path: up"])
    claim.pop("budget_s")
    claim.pop("population")
    out = mod.evaluate(claim, MEASUREMENTS)
    reasons = _joined(out)
    assert out["verdict"] == mod.VERDICT_REFUSED, (out, reasons)
    assert "budget_s" in reasons, reasons
    assert "population" in reasons, reasons
    full = _claim(exit_code=0, completed=True, observed_tokens=["inference path: up"])
    assert "missing" not in _joined(mod.evaluate(full, MEASUREMENTS)).lower(), reasons


def test_guessed_budget_is_refused():
    mod = _vocab()
    out = mod.evaluate(_claim(budget_source="guessed", exit_code=0, completed=True,
                              observed_tokens=["inference path: up"]), MEASUREMENTS)
    reasons = _joined(out)
    assert out["verdict"] == mod.VERDICT_REFUSED, (out, reasons)
    assert "guessed" in reasons, reasons


def test_measured_budget_with_no_measurement_is_refused():
    # budget_source='measured' with no row for this command is a guess wearing the word
    # measured -- the state this finding was left in: no dry-run duration exists anywhere
    # (the task's own checklist item 2 says so). It must not invent one.
    mod = _vocab()
    out = mod.evaluate(_claim(exit_code=0, completed=True,
                              observed_tokens=["inference path: up"]), [])
    reasons = _joined(out)
    assert out["verdict"] == mod.VERDICT_REFUSED, (out, reasons)
    assert "no measured" in reasons, reasons
    assert "610" not in reasons, reasons


def test_budget_below_the_measured_worst_case_is_refused():
    # The specimen's own number: 280s imposed on a command whose collectors alone sum to
    # 240s and whose probe is documented at 600s.
    mod = _vocab()
    out = mod.evaluate(_claim(budget_s=280, exit_code=0, completed=True,
                              observed_tokens=["inference path: up"]), MEASUREMENTS)
    reasons = _joined(out)
    assert out["verdict"] == mod.VERDICT_REFUSED, (out, reasons)
    assert "280" in reasons, reasons
    assert "610" in reasons, reasons


def test_budget_above_twice_the_measured_worst_case_is_refused():
    # The RCA's own stated direction (refuse a budget that EXCEEDS the measured worst
    # case): padding is not a measurement either. 1800 > 2 * 610.
    mod = _vocab()
    out = mod.evaluate(_claim(budget_s=1800, exit_code=0, completed=True,
                              observed_tokens=["inference path: up"]), MEASUREMENTS)
    assert out["verdict"] == mod.VERDICT_REFUSED, out


def test_measured_worst_case_is_matched_on_the_command_string():
    mod = _vocab()
    rows = [{"command": OTHER, "seconds": 5.0}, {"command": CMD, "seconds": MEASURED}]
    assert mod.measured_worst_case(CMD, rows) == 610.0
    assert mod.measured_worst_case(OTHER, rows) == 5.0
    assert mod.measured_worst_case("python3 scripts/scope_queue.py", rows) is None


def test_a_complete_measured_run_is_verified_with_no_reasons():
    mod = _vocab()
    out = mod.evaluate(_claim(exit_code=0, completed=True,
                              observed_tokens=["inference path: up", "PRIORITY"]),
                       MEASUREMENTS)
    assert out["verdict"] == mod.VERDICT_VERIFIED, out
    assert out["reasons"] == [], out


def test_the_vocabulary_is_added_not_replaced():
    # The file's existing vocabulary is load-bearing for six other suites: a build that
    # rewrites the module to satisfy this one fails here. Values read from the file itself
    # (its DISPOSITIONS and _KIND_DISPOSITION constants).
    mod = _vocab()
    assert list(mod.DISPOSITIONS)[0] == "unresolved", mod.DISPOSITIONS
    assert len(mod.DISPOSITIONS) == 5, mod.DISPOSITIONS
    assert mod._KIND_DISPOSITION["deferral"] == "deferred", mod._KIND_DISPOSITION
    for name in ("turns", "chunks", "enqueue", "discover", "extract", "extract_scripted",
                 "triage", "prove_cover", "_keep", "_json_list", "ask", "main"):
        assert callable(getattr(mod, name, None)), "lost " + name


TESTS = (
    test_the_vocabulary_names_exist,
    test_verdict_vocabulary_is_three_distinct_values,
    test_killed_run_is_unverified_and_names_the_exit_code,
    test_exit_zero_with_no_named_token_is_unverified,
    test_incomplete_run_is_unverified_even_with_output,
    test_missing_fields_are_named_one_by_one,
    test_guessed_budget_is_refused,
    test_measured_budget_with_no_measurement_is_refused,
    test_budget_below_the_measured_worst_case_is_refused,
    test_budget_above_twice_the_measured_worst_case_is_refused,
    test_measured_worst_case_is_matched_on_the_command_string,
    test_a_complete_measured_run_is_verified_with_no_reasons,
    test_the_vocabulary_is_added_not_replaced,
)

if __name__ == "__main__":
    failed = 0
    for fn in TESTS:
        try:
            fn()
            print("ok   %s" % fn.__name__)
        except Exception as exc:                                # a crash is a failure, not a pass
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
    print("\n%d/%d passed" % (len(TESTS) - failed, len(TESTS)))
    raise SystemExit(1 if failed else 0)
