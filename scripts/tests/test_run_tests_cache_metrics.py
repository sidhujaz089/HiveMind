#!/usr/bin/env python3
"""t1410 -- the suite runner records a MEASURED row for every --cache run.

The claim under test, verbatim from session be2121ac turn 243 (workspace/rca/c98b59b547e1.md
section 1): "So the saving shows up from the next session end onward". Nothing in this tree
stores suites-run vs suites-reused vs seconds for a suite-runner run, so that sentence can be
neither proven nor refuted. Today run_tests.py's only account of the saving is a printed line
that dies with the log it was printed into.

This suite is the recorder: run scripts/run_tests.py --cache TWICE in a temp repo over one tiny
suite, and require workspace/suite-cache-metrics.jsonl to carry one whole JSON row per run, the
second row measuring the reuse in numbers.

The runner is copied into the temp repo and run there, so nothing is written under this
repository. pytest is not installed; run it directly:
    python3 scripts/tests/test_run_tests_cache_metrics.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN_TESTS = ROOT / "scripts" / "run_tests.py"
sys.path.insert(0, str(ROOT / "scripts"))

# The row's fields, exactly, as the RCA's endpoint names them.
FIELDS = ("ts_utc", "suites_total", "suites_run", "suites_reused", "seconds",
          "cache_entries", "trigger")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
# A suite that passes, names no scripts/ path, and is not a coder_* sandbox suite (those never
# come from the cache), so the cache reuse is deterministic.
PROBE = "import sys\nprint('probe suite: ok')\nsys.exit(0)\n"


def make_repo(tmp):
    """A minimal repo-shaped tree. run_tests.py derives REPO from its own file location, so
    copying it here makes every path it writes land inside tmp."""
    root = Path(tmp) / "repo"
    (root / "scripts" / "tests").mkdir(parents=True)
    (root / "workspace").mkdir(parents=True)
    shutil.copyfile(RUN_TESTS, root / "scripts" / "run_tests.py")
    # suite_key() imports importgraph from REPO/scripts; a stub keeps the suite's input set to
    # the suite file itself, which is what makes the second run's key match the first.
    (root / "scripts" / "importgraph.py").write_text(
        "def reaches(rel):\n    return []\n", encoding="utf-8")
    (root / "scripts" / "tests" / "test_probe_ok.py").write_text(PROBE, encoding="utf-8")
    # known() reads the ceiling, and main() refuses to evaluate a full sweep without it.
    (root / "workspace" / "test-failures-ceiling.json").write_text(
        json.dumps({"allowed_to_fail": []}) + "\n", encoding="utf-8")
    return root


def runner(root, args, env_extra=None):
    env = dict(os.environ)
    env.pop("SUITE_RUN_TRIGGER", None)          # each call states its own trigger, explicitly
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(root / "scripts" / "run_tests.py"), *args],
                          capture_output=True, text=True, timeout=180,
                          cwd=str(root), env=env)


def rows_of(root):
    """The whole ledger, parsed. None when the file does not exist."""
    p = root / "workspace" / "suite-cache-metrics.jsonl"
    if not p.is_file():
        return None
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rows_from_pair():
    """One repo, two --cache runs, the rows they left. The first run records a pass, the second
    reuses it -- the pair the claim 'the saving shows up from the next session end onward'
    needs numbers for."""
    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(tmp)
        first = runner(root, ["--cache", "-k", "probe_ok"])
        second = runner(root, ["--cache", "-k", "probe_ok"])
        for tag, r in (("first", first), ("second", second)):
            assert r.returncode == 0, (
                f"the {tag} 'run_tests.py --cache -k probe_ok' run exited {r.returncode} in the "
                f"temp repo, so the pair cannot be judged.\n"
                f"--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}")
        rows = rows_of(root)
        assert rows is not None, (
            "no workspace/suite-cache-metrics.jsonl was written by two --cache runs. This is "
            "the whole point of t1410: without the ledger the 'faster from next time' claim has "
            "no recorder.\n--- stdout of run 1 ---\n" + first.stdout +
            "\n--- stdout of run 2 ---\n" + second.stdout)
        return rows


def test_one_row_per_cache_run():
    """The file exists, with exactly one row per --cache run: two runs, two rows."""
    rows = _rows_from_pair()
    assert len(rows) == 2, (
        f"expected one row per --cache run (2 rows), found {len(rows)}: {json.dumps(rows)}")


def test_row_carries_every_field_whole():
    """No row may be missing a field, carry an extra one, abbreviate the timestamp, or hold a
    count that is not a count. The full value, never a slice: a truncated row is the defect
    this task is about, reproduced in the record of the fix."""
    rows = _rows_from_pair()
    for i, row in enumerate(rows):
        assert isinstance(row, dict), f"row {i} is not a JSON object: {row!r}"
        assert set(row) == set(FIELDS), (
            f"row {i} has fields {sorted(row)}, expected exactly {sorted(FIELDS)}")
        assert TS_RE.match(str(row["ts_utc"])), (
            f"row {i} ts_utc {row['ts_utc']!r} is not a full UTC second "
            f"(YYYY-MM-DDTHH:MM:SSZ); a date-only stamp cannot order two runs in one day")
        for key in ("suites_total", "suites_run", "suites_reused", "cache_entries"):
            assert isinstance(row[key], int) and not isinstance(row[key], bool), (
                f"row {i} {key}={row[key]!r} is not an int")
        assert isinstance(row["seconds"], (int, float)), (
            f"row {i} seconds={row['seconds']!r} is not a number")
        assert isinstance(row["trigger"], str) and row["trigger"], (
            f"row {i} trigger={row['trigger']!r} is not a non-empty string")


def test_second_run_measures_the_reuse():
    """The measurement the claim needs: run 1 runs the suite and reuses nothing; run 2 runs
    nothing and reuses the suite. suites_total is the suites the run was asked to cover, in
    both rows -- the denominator the saving is a fraction of."""
    rows = _rows_from_pair()
    first, second = rows[0], rows[1]
    assert (first["suites_total"], first["suites_run"], first["suites_reused"]) == (1, 1, 0), (
        f"the first --cache run should record total=1 run=1 reused=0, got "
        f"total={first['suites_total']} run={first['suites_run']} "
        f"reused={first['suites_reused']}. It runs everything the first time.")
    assert (second["suites_total"], second["suites_run"], second["suites_reused"]) == (1, 0, 1), (
        f"the second --cache run over unchanged inputs should record total=1 run=0 reused=1 -- "
        f"that is the saving, in numbers -- got total={second['suites_total']} "
        f"run={second['suites_run']} reused={second['suites_reused']}. "
        f"If reused is 0 the row was written from data that does not know about the cache.")
    assert first["cache_entries"] == 1, (
        f"row 1 cache_entries={first['cache_entries']!r}, expected 1: the run recorded its one "
        f"passing suite, so a reader can see the cache it reused from next time")
    assert second["cache_entries"] == 1, (
        f"row 2 cache_entries={second['cache_entries']!r}, expected 1: reusing a pass must not "
        f"add an entry for the suite it did not run")


def test_seconds_is_measured_not_declared():
    """A seconds field that is always 0 is a placeholder, not a measurement."""
    rows = _rows_from_pair()
    for i, row in enumerate(rows):
        assert 0 < row["seconds"] < 180, (
            f"row {i} seconds={row['seconds']!r}: a --cache run over one suite takes seconds; "
            f"0 means the field is written, not measured")


def test_trigger_names_the_invoker():
    """session_end's closing summary must be able to attribute a row. The label comes from the
    environment so the runner needs no knowledge of its callers; a run with no label says 'cli',
    not '' and not a guess."""
    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(tmp)
        r = runner(root, ["--cache", "-k", "probe_ok"],
                   env_extra={"SUITE_RUN_TRIGGER": "session-end"})
        assert r.returncode == 0, (
            f"--cache run with SUITE_RUN_TRIGGER=session-end exited {r.returncode}\n"
            f"--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}")
        rows = rows_of(root)
        assert rows and len(rows) == 1, f"expected 1 row, got {json.dumps(rows)}"
        assert rows[0]["trigger"] == "session-end", (
            f"trigger={rows[0]['trigger']!r}, expected 'session-end' from "
            f"SUITE_RUN_TRIGGER. The runner cannot know who called it, so the label is read "
            f"from the environment -- that env var is the seam session_end's invocation uses.")
    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(tmp)
        r = runner(root, ["--cache", "-k", "probe_ok"])
        assert r.returncode == 0, f"plain --cache run exited {r.returncode}\n{r.stdout}\n{r.stderr}"
        rows = rows_of(root)
        assert rows and rows[0]["trigger"] == "cli", (
            f"trigger={rows[0]['trigger']!r}, expected the default 'cli' when "
            f"SUITE_RUN_TRIGGER is unset")


def test_a_run_without_the_cache_flag_records_no_row():
    """NEGATIVE assertion: the ledger is one row per --cache run, not one row per run. A
    non-cache run runs everything from zero, so it has no reuse to measure and must not dilute
    the record the claim is read out of."""
    with tempfile.TemporaryDirectory() as tmp:
        root = make_repo(tmp)
        r = runner(root, ["-k", "probe_ok"])
        assert r.returncode == 0, f"plain run exited {r.returncode}\n{r.stdout}\n{r.stderr}"
        rows = rows_of(root)
        assert rows is None, (
            f"a run WITHOUT --cache appended {len(rows)} row(s): {json.dumps(rows)}. The task "
            f"is about cache reuse; a row with no cache pass to measure is noise in the ledger "
            f"the claim is read from.")


def test_metrics_ledger_is_not_part_of_the_tree_identity():
    """The runner's tree id hashes every file in the repo it did not exclude. The metrics ledger
    is written BY the runner while it runs, so leaving it out of _SELF_WRITTEN makes the tree id
    change on every --cache run and silently defeats --resume and --assert-fresh -- the defect
    the comment above _SELF_WRITTEN documents for sweep-journal.jsonl and flaky-tests.log."""
    import run_tests
    assert hasattr(run_tests, "METRICS"), (
        "run_tests.py has no METRICS constant, so no row can be written anywhere -- "
        "it must name REPO / 'workspace' / 'suite-cache-metrics.jsonl', next to CACHE")
    rel = Path(run_tests.METRICS).resolve().relative_to(Path(run_tests.REPO).resolve()).as_posix()
    assert rel == "workspace/suite-cache-metrics.jsonl", (
        f"METRICS resolves to {rel!r}, expected 'workspace/suite-cache-metrics.jsonl'")
    assert rel in run_tests._SELF_WRITTEN, (
        f"{rel} is not in _SELF_WRITTEN ({sorted(run_tests._SELF_WRITTEN)}). The runner writes "
        f"that file itself, so tree_id() hashes its own output and the tree id changes the "
        f"moment a --cache run lands. Add the repo-relative posix path to that set.")


if __name__ == "__main__":
    tests = [test_one_row_per_cache_run,
             test_row_carries_every_field_whole,
             test_second_run_measures_the_reuse,
             test_seconds_is_measured_not_declared,
             test_trigger_names_the_invoker,
             test_a_run_without_the_cache_flag_records_no_row,
             test_metrics_ledger_is_not_part_of_the_tree_identity]
    failed = []
    for t in tests:
        try:
            t()
            print(f"ok    {t.__name__}")
        except AssertionError as e:
            failed.append(t.__name__)
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
