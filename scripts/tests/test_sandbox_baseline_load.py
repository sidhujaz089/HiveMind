#!/usr/bin/env python3
"""t853 -- the sandbox baseline contract must name LOAD, and the gate must record the event.

RCA 00cefde9445e. workspace/sandbox-baseline.json declares only permanent environmental reasons
(`cannot_run_in_sandbox`). A sibling suite that flips red under box load is therefore always
"undeclared", and coder.py's baseline gate refuses the dispatch -- box load becomes a red
test_coder_bad_shape. Nothing on disk joins refusal -> tree -> suite -> load, so no future RCA
can prove "it was load".

Two pieces are missing, both in projects/local-inference/coder.py:
  * baseline_findings() must accept a `load_sensitive` declaration and must NOT report those
    suites as undeclared -- and must keep reporting everything else.
  * record_baseline_event() must append one JSONL row per baseline evaluation carrying the load
    it saw, to a caller-supplied path.

Run: python3 scripts/tests/test_sandbox_baseline_load.py
No pytest, no model call, no live server, no process left running, no fixed /tmp path: every
write goes under tempfile.TemporaryDirectory().
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

REQUIRED_KEYS = ["at", "tree", "suite", "rc", "load1", "load5",
                 "n_model_jobs_running", "declared", "action"]

# Real suite names from scripts/tests/ (both exist on main) so no placeholder string is
# reused for two roles: LOAD_SENSITIVE names a coder-driving suite, UNDECLARED a different one.
LOAD_SENSITIVE = "test_coder_endpoint_drop.py"
UNDECLARED = "test_coder_green_count.py"

FAILS = []


def ck(label, got, want):
    """One line per finding, with both values, so a failure says enough to act on."""
    if got != want:
        FAILS.append(f"{label}\n      got:  {got!r}\n      want: {want!r}")


def sp(name):
    """The form run_suites() reports: a repo-relative path, not a bare filename."""
    return f"scripts/tests/{name}"


def rows_of(path):
    return [json.loads(line) for line in
            Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def test_load_sensitive_suite_is_not_an_undeclared_failure():
    ck("a suite declared load_sensitive must not be reported as an undeclared failure",
       coder.baseline_findings([sp(LOAD_SENSITIVE)], set(), set(),
                               load_sensitive={LOAD_SENSITIVE}), [])


def test_undeclared_failure_is_still_reported():
    # Negative assertion: the change must not make the gate blind. A suite named nowhere
    # in any of the three declarations must still stop the run.
    ck("a failing suite declared nowhere must still stop the run",
       coder.baseline_findings([sp(LOAD_SENSITIVE)], set(), set()),
       [f"undeclared failure: {LOAD_SENSITIVE}"])


def test_only_the_undeclared_one_is_reported():
    # Two distinct real values: declaring one suite must not excuse the other.
    ck("declaring one suite load_sensitive must not excuse a second failing suite",
       coder.baseline_findings([sp(LOAD_SENSITIVE), sp(UNDECLARED)], set(), set(),
                               load_sensitive={LOAD_SENSITIVE}),
       [f"undeclared failure: {UNDECLARED}"])


def test_load_sensitive_accepts_the_path_form_run_suites_returns():
    # run_suites reports 'scripts/tests/x.py'; the JSON declaration is bare. The existing
    # bare() normalisation must apply to the new set as well as to the other two.
    ck("a load_sensitive entry written as a path must match a path-form failure",
       coder.baseline_findings([sp(LOAD_SENSITIVE)], set(), set(),
                               load_sensitive={sp(LOAD_SENSITIVE)}), [])


def test_ceiling_target_and_passed_behaviour_unchanged():
    # The behaviour that already works, pinned so the change cannot quietly edit it.
    ck("a suite in the ceiling is still declared",
       coder.baseline_findings([sp(LOAD_SENSITIVE)], {sp(LOAD_SENSITIVE)}, set()), [])
    ck("the target suite is still red-at-baseline by definition",
       coder.baseline_findings([sp(UNDECLARED)], set(), set(), target=UNDECLARED), [])
    ck("a declared cannot-run suite that PASSES is still a finding",
       coder.baseline_findings([], set(), {sp(UNDECLARED)}, passed=[sp(UNDECLARED)]),
       [f"declared cannot-run but passed: {UNDECLARED}"])


def test_event_writer_appends_one_full_row():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "events.jsonl"
        coder.record_baseline_event(f, tree="abc123def456", suite=sp(LOAD_SENSITIVE),
                                    rc=1, ceiling=set(), cannot=set(),
                                    load_sensitive={LOAD_SENSITIVE}, action="refuse",
                                    n_jobs=3)
        rows = rows_of(f)
        ck("one evaluation leaves exactly one row", len(rows), 1)
        if not rows:
            return
        row = rows[0]
        ck("the row carries the declared contract, no more and no less",
           sorted(row), sorted(REQUIRED_KEYS))
        ck("tree recorded verbatim", row.get("tree"), "abc123def456")
        ck("suite recorded verbatim, not normalised", row.get("suite"), sp(LOAD_SENSITIVE))
        ck("rc recorded", row.get("rc"), 1)
        ck("action recorded", row.get("action"), "refuse")
        ck("n_model_jobs_running recorded", row.get("n_model_jobs_running"), 3)
        ck("declared computed from the load_sensitive set", row.get("declared"),
           "load-sensitive")
        ck("at is a non-empty string",
           isinstance(row.get("at"), str) and len(row.get("at")) > 0, True)
        ck("load1 is a number, not a placeholder",
           isinstance(row.get("load1"), (int, float)) and row.get("load1") >= 0, True)
        ck("load5 is a number, not a placeholder",
           isinstance(row.get("load5"), (int, float)) and row.get("load5") >= 0, True)


def test_event_writer_appends_and_does_not_overwrite():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "events.jsonl"
        coder.record_baseline_event(f, tree="t", suite=sp(LOAD_SENSITIVE), rc=1,
                                    ceiling=set(), cannot=set(),
                                    load_sensitive={LOAD_SENSITIVE}, action="refuse",
                                    n_jobs=0)
        coder.record_baseline_event(f, tree="t", suite=sp(UNDECLARED), rc=0,
                                    ceiling={UNDECLARED}, cannot=set(),
                                    load_sensitive=set(), action="proceed", n_jobs=0)
        rows = rows_of(f)
        ck("a second evaluation appends a second row", len(rows), 2)
        ck("the first row is kept", (rows[0] or {}).get("suite"), sp(LOAD_SENSITIVE))
        ck("the second row is recorded", (rows[1] or {}).get("suite"), sp(UNDECLARED))
        ck("the second row kept its own rc", (rows[1] or {}).get("rc"), 0)


def test_declared_field_distinguishes_the_three_kinds():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "events.jsonl"
        coder.record_baseline_event(f, tree="t", suite=sp(LOAD_SENSITIVE), rc=1,
                                    ceiling=set(), cannot=set(),
                                    load_sensitive={LOAD_SENSITIVE}, action="refuse")
        coder.record_baseline_event(f, tree="t", suite=sp(UNDECLARED), rc=1,
                                    ceiling={UNDECLARED}, cannot=set(),
                                    load_sensitive=set(), action="proceed")
        coder.record_baseline_event(f, tree="t", suite="test_unknown_kind.py", rc=1,
                                    ceiling=set(), cannot=set(),
                                    load_sensitive=set(), action="refuse")
        rows = rows_of(f)
        ck("three evaluations append three rows", len(rows), 3)
        ck("the three declarations produce three distinct values",
           [r.get("declared") for r in rows], ["load-sensitive", "yes", "no"])


def test_declared_field_says_yes_for_a_cannot_run_suite():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "events.jsonl"
        coder.record_baseline_event(f, tree="t", suite=sp(UNDECLARED), rc=1,
                                    ceiling=set(), cannot={UNDECLARED},
                                    load_sensitive=set(), action="refuse")
        rows = rows_of(f)
        ck("a declared cannot-run suite is 'yes', not 'no'",
           (rows[0] if rows else {}).get("declared"), "yes")


def test_unknown_job_count_is_recorded_as_none_not_omitted():
    # The key must be present when the caller has no count: an absent key is the shape of
    # "this field was never part of the record", a null is the shape of "we did not know".
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "events.jsonl"
        coder.record_baseline_event(f, tree="t", suite=sp(LOAD_SENSITIVE), rc=0,
                                    ceiling=set(), cannot=set(),
                                    load_sensitive={LOAD_SENSITIVE}, action="proceed")
        rows = rows_of(f)
        ck("n_model_jobs_running is present when no count is supplied",
           "n_model_jobs_running" in (rows[0] if rows else {}), True)
        ck("its value is None, so a missing count is visible, not absent",
           (rows[0] if rows else {}).get("n_model_jobs_running"), None)


def test_at_is_a_real_timestamp_not_an_empty_placeholder():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "events.jsonl"
        coder.record_baseline_event(f, tree="t", suite=sp(LOAD_SENSITIVE), rc=1,
                                    ceiling=set(), cannot=set(),
                                    load_sensitive={LOAD_SENSITIVE}, action="refuse")
        rows = rows_of(f)
        at = (rows[0] if rows else {}).get("at")
        ck("at carries a 4-digit year",
           isinstance(at, str) and len(at) >= 10 and at[:4].isdigit(), True)


TESTS = [v for k, v in sorted(globals().items())
         if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    for t in TESTS:
        try:
            t()
        except Exception as e:                      # a missing function is a finding, not a crash
            FAILS.append(f"{t.__name__} raised {type(e).__name__}: {e}")
    for msg in FAILS:
        print("[BAD] " + msg)
    print(f"test_sandbox_baseline_load: {len(TESTS)} tests, {len(FAILS)} failure(s) -- "
          + ("FAILED" if FAILS else "ok"))
    sys.exit(1 if FAILS else 0)
