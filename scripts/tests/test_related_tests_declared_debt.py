#!/usr/bin/env python3
"""test_related_tests_declared_debt.py -- oracle for t1009: a suite that is DECLARED red
is reported, not blocking, at the commit gate.

RED ON MAIN, and red for the missing change. `scripts/related_tests.py:112` builds its
known-debt set from ONE key of workspace/test-failures-ceiling.json:

    known = set(json.loads(CEILING.read_text(encoding="utf-8")).get("allowed_to_fail") or [])

The same file carries a second declared-debt registry, `_oracles_awaiting_their_fix` (306
entries today; `test_coder_budget_clamp.py` is one of them, line 83, with its reason). A
suite listed THERE is, by that registry's own meaning, a suite that is red ON PURPOSE and
awaits its fix. The commit gate does not read that key, so it treats the declared suite as
fresh debt and returns 1 -- and the hook refuses a commit whose code change the reviewer
ACCEPTED. That is what happened to this task: dispatch
`p356-t1009-coder--production--swift--1791608307.log` records `attempt 4 ... ACCEPTED ... 400
suite(s) green` and then `commit refused: COMMIT BLOCKED -- a staged script turns a suite that
uses it red -- every coder build would refuse`, leaving no branch.

WHAT THIS SUTES. It runs the REAL `main()` in-process against a throwaway repo: stub suite
FILES (not mocks) executed the way `run()` runs them, so a change that hardcodes a pass, a
change that ignores the stub's exit code, and a change that skips the ceiling entirely all
fail here. The red stub is named WITHOUT the substring "coder", because
`related_tests.py:119-122` excludes any suite containing "coder" from the commit-time run --
naming it `test_coder_*` would test the exclusion, not the ceiling, and pass on main.

Red on main: test 1 (a declared-red suite must not block) fails for the stated reason. Tests
2-4 pass on main -- they are the decay guards for the new code, and a suite that fails them is
not a fix. Each prints its own FAIL line, so `python3 <this file>` shows WHICH assertion broke.

Reads and writes only inside tempfile.mkdtemp dirs; restores every patched module global in a
finally block; spawns nothing outside `run()`'s own timeout-bearing subprocess.run. No model
call, no live server, no fixed /tmp path, no process left running.

Run: python3 scripts/tests/test_related_tests_declared_debt.py
"""
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO_SRC = ROOT / "scripts"
CEILING_NAME = "test-failures-ceiling.json"

ALPHA = "test_alpha_uses_it.py"
BETA = "test_beta_uses_it.py"

# A stub that fails, and a stub that passes: two DISTINCT outcomes, so a suite that reports
# "known debt" for everything, and one that reports nothing, are both caught.
STUB_RED = "# exercises coder.py\nimport sys\nprint('  RED: boom')\nsys.exit(1)\n"
STUB_GREEN = "# exercises coder.py\nimport sys\nsys.exit(0)\n"

# The staged file whose dependents are being checked. `related()` (scripts/related_tests.py:60)
# maps a staged path to its stem and matches test files whose text contains `<stem>.py`, so the
# stubs above carry that string in a comment -- that is how they become "suites that use it".
STAGED = ["projects/local-inference/coder.py"]


def _fixture(alpha_src, beta_src, ceiling_body):
    """A throwaway repo shaped the way related_tests expects: a tests/ dir with real suite
    files, a workspace/ ceiling file, and the staged project path present on disk."""
    repo = Path(tempfile.mkdtemp(prefix="t1009-"))
    (repo / "scripts" / "tests").mkdir(parents=True)
    (repo / "workspace").mkdir(parents=True)
    (repo / "projects" / "local-inference").mkdir(parents=True)
    (repo / STAGED[0]).write_text("# stub for t1009\n", encoding="utf-8")
    (repo / "scripts" / "tests" / ALPHA).write_text(alpha_src, encoding="utf-8")
    (repo / "scripts" / "tests" / BETA).write_text(beta_src, encoding="utf-8")
    (repo / "workspace" / CEILING_NAME).write_text(
        json.dumps(ceiling_body), encoding="utf-8")
    return repo


def _main(repo, ceiling_body, staged):
    """Run the REAL related_tests.main() with its module globals pointed at the throwaway repo,
    then restore them. Patching globals (not the stubbed module) is what exercises the code
    under test: main() reads CEILING, TESTS and REPO, and run() spawns from TESTS."""
    sys.path.insert(0, str(REPO_SRC))
    import related_tests as rt
    saved = (rt.REPO, rt.TESTS, rt.CEILING)
    rt.REPO = repo
    rt.TESTS = repo / "scripts" / "tests"
    rt.CEILING = repo / "workspace" / CEILING_NAME
    rt.CLEAN_ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    out = []
    old = sys.stdout
    try:
        class _Tee:
            encoding = "utf-8"
            def write(self, s):
                out.append(s)
                return len(s)
            def flush(self):
                pass
        sys.stdout = _Tee()
        rc = rt.main(list(staged))
    finally:
        sys.stdout = old
        rt.REPO, rt.TESTS, rt.CEILING = saved
    return rc, "".join(out)


def _ceiling(allowed, awaiting):
    # The real file's shape: both keys present, values lists of suite names.
    return {"allowed_to_fail": list(allowed), "_oracles_awaiting_their_fix": list(awaiting)}


def test_a_declared_red_suite_is_reported_not_blocking():
    """THE CASE THIS EXISTS FOR: a dependent suite exits 1 and the ceiling declares it under
    `_oracles_awaiting_their_fix`. The commit must be allowed and the suite reported in the
    SAME wording the gate already uses for `allowed_to_fail` -- one report, not two dialects.
    RED ON MAIN: today `known` comes from `allowed_to_fail` alone, so alpha is fresh debt and
    the return code is 1."""
    repo = _fixture(STUB_RED, STUB_GREEN,
                    _ceiling([], [ALPHA]))
    try:
        rc, out = _main(repo, None, STAGED)
        assert rc == 0, (
            "a suite declared red in the ceiling's `_oracles_awaiting_their_fix` must NOT "
            "block the commit -- that registry exists to say 'this suite is red on purpose, "
            "its fix is queued'. The build that died here was ACCEPTED at 400 green suites. "
            "rc=%r output:\n%s" % (rc, out))
        assert ("known debt, not blocking: " + ALPHA) in out, (
            "the suite must be REPORTED, not silently skipped, and in the wording "
            "scripts/related_tests.py already prints for `allowed_to_fail` (its line 132), so "
            "one reader sees one format. output:\n" + out)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_an_undeclared_red_suite_still_blocks():
    """NEGATIVE -- the load-bearing guard. A red suite in NEITHER registry must return 1 and
    print its RED line. A fix that exempts every failure passes test 1 and fails this one,
    which is the difference between honouring the ceiling and disabling the gate (the bypass
    trap: a gate that fires too often gets --no-verify'd within a week; a gate that never
    fires is worth less than nothing)."""
    repo = _fixture(STUB_RED, STUB_GREEN, _ceiling([], []))
    try:
        rc, out = _main(repo, None, STAGED)
        assert rc == 1, (
            "a suite that is declared NOWHERE must still refuse the commit; rc=%r. The t650b "
            "guarantee (this file's docstring: 'A COMMIT MUST NOT TURN MAIN RED UNNOTICED') "
            "is not negotiable. output:\n%s" % (rc, out))
        assert ("RED: " + ALPHA) in out, (
            "a blocked commit must name the suite that is red, in the format the real "
            "scripts/related_tests.py prints (two spaces, 'RED: ', bare name) -- that string "
            "is what the landing-ledger parse reads. output:\n" + out)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_allowed_to_fail_still_works_unchanged():
    """REGRESSION: the existing registry keeps working on its own, with the new key absent
    from the file entirely -- an older ceiling file, and the shape today's file already has
    for most suites. A refactor that reads only the new key fails here."""
    repo = _fixture(STUB_RED, STUB_GREEN, {"allowed_to_fail": [ALPHA]})
    try:
        rc, out = _main(repo, None, STAGED)
        assert rc == 0, (
            "`allowed_to_fail` must be honoured exactly as it is today; rc=%r. output:\n%s"
            % (rc, out))
        assert ("known debt, not blocking: " + ALPHA) in out, out
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def test_a_green_suite_writes_no_debt_report():
    """NEGATIVE -- the report is for failures, not for every suite. With both stubs green the
    run must exit 0 and name no suite as debt; a change that prints a debt line per suite is
    the noise that gets this gate routed around."""
    repo = _fixture(STUB_GREEN, STUB_GREEN,
                    _ceiling([], [ALPHA, BETA]))
    try:
        rc, out = _main(repo, None, STAGED)
        assert rc == 0, "all-green dependents must not block the commit; rc=%r\n%s" % (rc, out)
        assert "known debt" not in out, (
            "nothing failed, so nothing may be reported as debt. output:\n" + out)
        assert ("RED: " not in out), output
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def main():
    tests = [test_a_declared_red_suite_is_reported_not_blocking,
             test_an_undeclared_red_suite_still_blocks,
             test_allowed_to_fail_still_works_unchanged,
             test_a_green_suite_writes_no_debt_report]
    failed = 0
    for t in tests:
        try:
            t()
            print("ok   " + t.__name__)
        except Exception:
            failed += 1
            print("FAIL " + t.__name__)
            traceback.print_exc()
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
