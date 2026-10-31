#!/usr/bin/env python3
"""t1023: coder.py must refuse a dispatch whose target suite cannot go GREEN.

Filed by the Q16 supervisor from p381-t1022-library-checks (swift, 2026-10-10T23:48:18Z):
the target suite was in the 89-suite baseline-failing set, the brief froze the oracle, and
attempts 1-7 burned on an acceptance criterion no in-scope edit can satisfy.

RED ON MAIN because coder.py has no `target_unreachable` (verified: grep "def target_unreachable"
over projects/local-inference/coder.py returns nothing; the file has 3140 lines and its baseline
call site `already, _ = run_suites(...)` at line 2565 discards the per-suite output this check
needs). Every assertion below is on coder.py's real importable module.
"""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]           # scripts/tests/ -> repo root
HERE = ROOT / "projects" / "local-inference" / "coder.py"

sys.path.insert(0, str(HERE.parent))
try:
    import coder
except Exception as e:                                # noqa: BLE001
    print(f"FATAL: cannot import coder.py from {HERE}: {type(e).__name__}: {e}")
    sys.exit(1)

# Real names from the filed case: the suite that was red at baseline, and the module the
# brief let the model edit. Distinct on purpose -- a stub that ignores `files` cannot pass
# both test_healthy and test_unreachable, and a stub that ignores the output text cannot
# pass test_no_output_is_not_evidence.
TARGET = "scripts/tests/test_t1022_endpoint_literal_is_not_a_call.py"
MODULE = "scripts/library_checks.py"
UNRELATED = "scripts/scope_queue.py"

# Traceback shapes a run_tests failure really produces: the frame prints the source line,
# so the module under test appears by name in the suite's own output.
RED_BECAUSE_OF_SCOPE = (
    'Traceback (most recent call last):\n'
    f'  File "{ROOT}/scripts/tests/test_t1022_endpoint_literal_is_not_a_call.py", line 41, in test_it\n'
    f'    assert coder_target.scan_endpoints(src) == set()\n'
    'AttributeError: module \'library_checks\' has no attribute \'scan_endpoints\'\n'
)
RED_ELSEWHERE = (
    'Traceback (most recent call last):\n'
    f'  File "{ROOT}/scripts/stranded_helpers.py", line 12, in <module>\n'
    '    from scripts import vanished_helper\n'
    'ModuleNotFoundError: No module named \'scripts.vanished_helper\'\n'
)


def _fn():
    fn = getattr(coder, "target_unreachable", None)
    assert fn is not None, "coder.py has no target_unreachable(target, out, files)"
    return fn


def test_unreachable_is_named_with_the_reason():
    """The filed case: target red for a cause outside the brief's files -> a reason string."""
    fn = _fn()
    reason = fn(TARGET, {TARGET: RED_ELSEWHERE}, [UNRELATED])
    assert isinstance(reason, str) and reason.strip(), \
        "must return a non-empty reason string, not True"
    assert TARGET in reason, f"reason must name the suite it cannot turn green: {reason!r}"
    assert "GREEN" in reason, \
        f"reason must name the acceptance criterion (target goes GREEN): {reason!r}"
    assert UNRELATED in reason, f"reason must name the files the run was given: {reason!r}"


def test_healthy_fail_first_must_not_be_refused():
    """The negative that stops the over-refusal: EVERY dispatch's target is red at baseline
    (the entry condition comment above the run_suites baseline call -- 'the entry condition,
    not scenery'). A target red for a cause inside a file in scope is solvable and must stay
    runnable, or this gate halts all work."""
    fn = _fn()
    assert fn(TARGET, {TARGET: RED_BECAUSE_OF_SCOPE}, [MODULE]) is None, \
        "a target whose failure names a file in scope is the normal case; refusing it " \
        "refuses every fail-first dispatch"


def test_scope_widened_to_the_real_owner_is_solvable():
    """The remedy this guard tells the scoper to apply: widen the brief's files until the
    target's failure names one of them. A list of several, one matching, is solvable."""
    fn = _fn()
    assert fn(TARGET, {TARGET: RED_BECAUSE_OF_SCOPE},
              [UNRELATED, "scripts/library_checks.py"]) is None


def test_no_output_is_not_evidence():
    """Absence of the record is not proof of impossibility. A suite that dies before printing,
    or a target green at baseline (absent from the output map), must never be refused."""
    fn = _fn()
    assert fn(TARGET, {}, [MODULE]) is None
    assert fn(TARGET, {TARGET: ""}, [MODULE]) is None


def test_targetless_run_is_untouched():
    fn = _fn()
    assert fn(None, {TARGET: RED_ELSEWHERE}, [UNRELATED]) is None
    assert fn("", {TARGET: RED_ELSEWHERE}, [UNRELATED]) is None


def test_files_out_of_scope_are_ignored_as_evidence():
    """The target suite's own name in its traceback is not scope: the failing frame is always
    in the test file, so matching it would report every red target as solvable."""
    fn = _fn()
    only_self = f'AssertionError: file "{TARGET}" is not a call\n'
    reason = fn(TARGET, {TARGET: only_self}, [TARGET])
    assert isinstance(reason, str) and reason.strip(), \
        "naming the target suite itself as the editable file is not a fix"


def test_function_is_pure_and_never_writes():
    """No live state: the predicate must not touch disk, a model, or the tree it is given.
    It is called on the sandbox's output dict inside the attempt loop's reach."""
    fn = _fn()
    import inspect
    src = inspect.getsource(fn)
    for banned in ("urlopen", "subprocess", "Popen", "write_text", "mkdir", "Ollama",
                   "requests."):
        assert banned not in src, f"target_unreachable must not do I/O ({banned})"
    out = {TARGET: RED_ELSEWHERE}
    fn(TARGET, out, [UNRELATED])
    assert out == {TARGET: RED_ELSEWHERE}, "must not mutate the output dict"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:                        # noqa: BLE001
            failed.append(name)
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
