"""t1354 -- the acceptance verdict must not blame a dispatch for declared pre-existing debt.

A dispatch scoped to ONE file is refused for a suite it cannot reach. Measured on
p606-t1030-handover (2026-10-17T09:06:09Z): the brief edited scripts/handover.py, the
target suite was scripts/tests/test_handover_coverage.py, and the harness reported
"tests fail (1: test_slot_concurrency.py, 82s)" on attempt 1 and
"tests fail (1: test_fast_path.py, 116s)" on attempt 3. Both suites are declared
allowed-to-fail in workspace/test-failures-ceiling.json, so they are known debt the
box-wide ratchet (run_tests.py) owns -- not regressions a one-file patch caused.

The defect is in projects/local-inference/coder.py at the acceptance site: the verdict
subtracts only the MEASURED baseline. The declared ceiling is read for the baseline guard
and is never consulted when the verdict is taken, so a suite that was green at baseline and
red at the verdict is "new". run_suites' own docstring says of this family of suites that
they fail only under contention -- several lanes each building a sandbox and running the
full suite at once.

RED ON MAIN for the missing change: coder.py has no `attributable_failures` (grep -rn
"attributable_failures" --include=*.py . over the repository returns nothing this session),
so every behavioural assertion below raises AttributeError, and the last test is red because
the old baseline-only expression is still in the file. The file is read-only for this suite:
it imports it, writes nothing, runs no subprocess, makes no network call.
"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests -> repo root
CODER = ROOT / "projects" / "local-inference" / "coder.py"
CEILING = ROOT / "workspace" / "test-failures-ceiling.json"

_CACHE = {}


def coder():
    """coder.py imported from the path it really lives on, once per run."""
    if "m" not in _CACHE:
        spec = importlib.util.spec_from_file_location("coder_under_test", CODER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _CACHE["m"] = mod
    return _CACHE["m"]


def test_the_module_imports_and_the_function_exists():
    mod = coder()
    assert hasattr(mod, "attributable_failures"), (
        "coder.py has no attributable_failures -- that is the change this brief asks for")


def test_a_suite_declared_in_the_ceiling_is_not_a_regression():
    """The p606 shape: the target is green, the only red suite is declared debt.

    Real constants from the ceiling (test_fast_path.py, test_slot_concurrency.py) and a
    distinct target, so a build that treats every failing suite as scenery cannot pass.
    """
    out = coder().attributable_failures(
        ["scripts/tests/test_fast_path.py"],
        baseline=[],
        target="scripts/tests/test_handover_coverage.py",
        ceiling=["scripts/tests/test_slot_concurrency.py", "scripts/tests/test_fast_path.py"])
    assert out == [], f"declared pre-existing debt was counted as a regression: {out}"


def test_a_new_failure_that_is_not_declared_is_still_a_regression():
    """The negative half. A build that exempts everything passes case 1 and fails here."""
    out = coder().attributable_failures(
        ["scripts/tests/test_t1354_not_in_the_ceiling.py"],
        baseline=[],
        target="scripts/tests/test_handover_coverage.py",
        ceiling=[])
    assert out == ["scripts/tests/test_t1354_not_in_the_ceiling.py"], out


def test_the_target_is_never_exempted_by_the_ceiling():
    """The over-relaxation to forbid: subtracting the ceiling from the target too ships a
    runner that ACCEPTs a dispatch whose own target suite is still red. A suite listed in
    the ceiling can BE the target -- in the real p606 case the target, scripts/tests/
    test_handover_coverage.py, is itself declared in workspace/test-failures-ceiling.json
    -- so this case uses a real ceiling entry as the target.
    """
    out = coder().attributable_failures(
        ["scripts/tests/test_slot_concurrency.py"],
        baseline=[],
        target="scripts/tests/test_slot_concurrency.py",
        ceiling=["scripts/tests/test_slot_concurrency.py"])
    assert out == ["scripts/tests/test_slot_concurrency.py"], out


def test_a_suite_red_before_the_model_touched_anything_is_not_a_regression():
    """The behaviour the baseline rule already guarantees must survive the change:
    test_a is baseline debt, test_b is a real regression, and order is preserved."""
    out = coder().attributable_failures(
        ["scripts/tests/test_a.py", "scripts/tests/test_b.py"],
        baseline=["scripts/tests/test_a.py"],
        target="scripts/tests/test_handover_coverage.py",
        ceiling=[])
    assert out == ["scripts/tests/test_b.py"], out


def test_bare_ceiling_names_are_matched_exactly_not_by_substring():
    """The ceiling stores bare file names (verified: every entry in allowed_to_fail is
    bare); `failed` carries scripts/tests/... paths. Matching by substring would exempt
    test_slot_concurrency_extras.py for a suite that has no relationship to the declared
    one -- the over-broad-match shape this family keeps producing."""
    coder_mod = coder()
    assert coder_mod.attributable_failures(
        ["scripts/tests/test_slot_concurrency.py"], baseline=[], target=None,
        ceiling=["test_slot_concurrency.py"]) == []
    assert coder_mod.attributable_failures(
        ["scripts/tests/test_slot_concurrency_extras.py"], baseline=[], target=None,
        ceiling=["test_slot_concurrency.py"]) == \
        ["scripts/tests/test_slot_concurrency_extras.py"]


def test_the_default_ceiling_is_the_real_declared_file():
    """With no ceiling argument the function reads workspace/test-failures-ceiling.json
    itself. Built from the file's own contents so this cannot freeze today's list: a build
    that defaults to an empty list fails here, and so does one that reads the wrong path."""
    declared = json.loads(CEILING.read_text(encoding="utf-8"))["allowed_to_fail"]
    assert declared, "workspace/test-failures-ceiling.json has no allowed_to_fail entries"
    entry = declared[0]
    as_path = entry if "/" in entry else "scripts/tests/" + entry
    coder_mod = coder()
    assert coder_mod.attributable_failures([as_path], baseline=[]) == []
    assert coder_mod.attributable_failures(
        ["scripts/tests/test_t1354_not_in_the_ceiling.py"], baseline=[]) == \
        ["scripts/tests/test_t1354_not_in_the_ceiling.py"]


def test_the_acceptance_site_uses_the_function_and_the_old_rule_is_gone():
    """A new function no caller calls is a decoration -- the check-satisfied-by-dead-path
    class. The verdict in main() must go through it, and the rule it replaces must leave
    the file, so the acceptance path cannot keep answering the question by itself."""
    src = CODER.read_text(encoding="utf-8")
    assert "attributable_failures(failed, already, target=target)" in src, (
        "the acceptance site in main() still decides the verdict on its own")
    assert "new_failures = [s for s in failed if s not in already]" not in src, (
        "the old baseline-only comparison is still in the file beside the new rule")


if __name__ == "__main__":
    names = sorted(k for k, v in globals().items() if k.startswith("test_") and callable(v))
    bad = 0
    for name in names:
        try:
            globals()[name]()
            print(f"ok   {name}")
        except AssertionError as e:
            bad += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:
            bad += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(names) - bad}/{len(names)} passed")
    raise SystemExit(1 if bad else 0)
