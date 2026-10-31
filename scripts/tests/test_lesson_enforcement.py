#!/usr/bin/env python3
"""LIVENESS of LESSONS.md ENFORCEMENT citations (t1362).

scripts/check_enforcement.py proves a cited file EXISTS (scripts/check_enforcement.py:97-101)
and that its NAME is MENTIONED (scripts/check_enforcement.py:104-105). Neither proves the
cited suite is a guard that FIRES. A citation that names a failing suite reads as coverage.
This suite pins the LIVENESS: the gate runs each named test and records its real exit code.

Run: python3 scripts/tests/test_lesson_enforcement.py   (pytest is not installed)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> the repo root
MODULE_PATH = ROOT / "scripts" / "check_lesson_enforcement.py"
TIMEOUT_S = 60

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def load():
    if not MODULE_PATH.exists():
        raise AssertionError(
            f"{MODULE_PATH} does not exist. The liveness gate for LESSONS.md ENFORCEMENT "
            f"citations has not been written yet (t1362).")
    spec = importlib.util.spec_from_file_location("check_lesson_enforcement", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# a REAL ENFORCEMENT block, copied verbatim from docs/LESSONS.md:2013-2014 [unverified line numbers]
L091_ENF = ('ENFORCEMENT: `taskstore.add_note` raises on `who="jas"` unless called with the '
             'form\'s internal\nflag; `scripts/tests/test_task_queue.py` asserts the refusal.\n')
# a real ENFORCEMENT line, copied verbatim from docs/LESSONS.md:1052
L_NONE_ENF = "ENFORCEMENT: none — judgement. Recorded because the failure was in how a decision was\n"
# a real ENFORCEMENT line naming a NON-test script, copied verbatim from docs/LESSONS.md:2519
L_SCRIPT_ENF = "ENFORCEMENT: `scripts/jscheck.py`, a JavaScript tokeniser, via\n"


def test_parses_real_citation_pairs():
    mod = load()
    pairs = mod.citations([("L-091", "2026-08-25", L091_ENF),
                           ("L-XX", "2026-08-25", L_NONE_ENF),
                           ("L-YY", "2026-08-25", L_SCRIPT_ENF)])
    check(("L-091", "scripts/tests/test_task_queue.py") in pairs,
          f"citations() did not pair L-091 with the file its ENFORCEMENT line names. Got {pairs!r}")
    check(len(pairs) == 1,
          f"exactly one citation is present in these three blocks; got {len(pairs)}: {pairs!r}. "
          f"'ENFORCEMENT: none' and a bare `taskstore.add_note` (no path, no '/') must yield none.")


def test_reads_the_real_lessons_file():
    mod = load()
    pairs = mod.citations(mod.lessons())
    check(("L-091", "scripts/tests/test_task_queue.py") in pairs,
          "citations(lessons()) over the REAL docs/LESSONS.md must yield "
          f"('L-091', 'scripts/tests/test_task_queue.py'). Got {pairs!r}")


def test_runs_a_passing_and_a_failing_cited_test():
    mod = load()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "scripts" / "tests").mkdir(parents=True)
        (root / "scripts" / "tests" / "test_green.py").write_text(
            "import sys\nprint('green')\nsys.exit(0)\n")
        (root / "scripts" / "tests" / "test_red.py").write_text(
            "import sys\nprint('red', file=sys.stderr)\nsys.exit(1)\n")
        ok = mod.run_citation(root, "scripts/tests/test_green.py", timeout=TIMEOUT_S)
        bad = mod.run_citation(root, "scripts/tests/test_red.py", timeout=TIMEOUT_S)
    check(isinstance(ok, int) and ok == 0,
          f"run_citation on a passing suite must return exit code 0; got {ok!r}")
    check(isinstance(bad, int) and bad == 1,
          f"run_citation on a failing suite must return exit code 1 -- the real return code, "
          f"not a bool, not 0; got {bad!r}")


def test_timeout_is_enforced_and_nothing_is_left_running():
    mod = load()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "scripts" / "tests").mkdir(parents=True)
        (root / "scripts" / "tests" / "test_hangs.py").write_text(
            "import time\ntime.sleep(120)\n")
        t0 = time.time()
        code = mod.run_citation(root, "scripts/tests/test_hangs.py", timeout=1)
        elapsed = time.time() - t0
    check(elapsed < 30,
          f"a hanging cited test must be cut off; run_citation took {elapsed:.1f}s with "
          f"timeout=1 (a timeout= on subprocess.run is required: a gate that hangs a "
          f"session_end run is a gate that gets --no-verify'd).")
    check(isinstance(code, int) and code != 0,
          f"a timed-out cited test must be reported as a failure, not 0; got {code!r}")


def test_non_test_citations_are_never_executed():
    mod = load()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "scripts").mkdir(parents=True)
        side = root / "RAN"
        (root / "scripts" / "do_the_thing.py").write_text(
            "import pathlib\npathlib.Path(__file__).with_name('RAN').write_text('yes')\n")
        code = mod.run_citation(root, "scripts/do_the_thing.py", timeout=TIMEOUT_S)
    check(code is None,
          f"a cited file that is not a test_*.py must not be executed -- it returns None and "
          f"is recorded statically; got {code!r}. Executing scripts at session_end is a "
          f"side-effect machine.")
    check(not side.exists(),
          f"{side} was created: the gate EXECUTED a non-test cited file. It must never run "
          f"anything whose name does not start with 'test_'.")


def test_rows_are_appended_and_complete():
    mod = load()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "scripts" / "tests").mkdir(parents=True)
        (root / "scripts" / "tests" / "test_green.py").write_text("raise SystemExit(0)\n")
        (root / "scripts" / "tests" / "test_red.py").write_text("raise SystemExit(3)\n")
        out = root / "workspace" / "lesson-enforcement.jsonl"
        lessons = [("L-091", "2026-08-25", L091_ENF),
                   ("L-999", "2026-08-25",
                    "ENFORCEMENT: `scripts/tests/test_red.py` guards it.\n")]
        rows = mod.collect(lessons=lessons, root=root, out=out)
        check(len(rows) == 2, f"one row per citation, including the failing one; got {rows!r}")
        codes = {r["file"]: r["exit_code"] for r in rows}
        check(codes.get("scripts/tests/test_green.py") == 0
              and codes.get("scripts/tests/test_red.py") == 3,
              f"rows must carry the DISTINCT real exit codes per file; got {codes!r}")
        for r in rows:
            check(set(r) >= {"lesson", "file", "command", "exit_code", "ts"},
                  f"row missing contract keys: {sorted(r)!r}")
        check(rows[0]["file"] != rows[1]["file"],
              "the two rows must name their own files -- one repeated placeholder proves nothing")
        check(out.exists(), f"no ledger written at {out}")
        first = out.read_text().splitlines()
        check(len(first) == 2, f"expected 2 jsonl lines, got {len(first)}")
        json.loads(first[0])                                  # each line is valid JSON
        rows2 = mod.collect(lessons=lessons, root=root, out=out)
        check(len(out.read_text().splitlines()) == 4,
              f"the second run must APPEND to the ledger, not rewrite it -- "
              f"expected 4 lines total, got {len(out.read_text().splitlines())}")
        check(len(rows2) == 2, f"collect() returns the rows it just wrote; got {rows2!r}")


def test_findings_naming_lesson_and_path():
    mod = load()
    rows = [{"lesson": "L-091", "file": "scripts/tests/test_task_queue.py",
             "command": "python3 scripts/tests/test_task_queue.py --fast", "exit_code": 1,
             "ts": "2026-10-17T00:00:00Z"},
            {"lesson": "L-092", "file": "scripts/tests/test_green.py",
             "command": "python3 scripts/tests/test_green.py --fast", "exit_code": 0,
             "ts": "2026-10-17T00:00:00Z"}]
    f = mod.findings(rows)
    check(any("L-091" in s and "test_task_queue.py" in s for s in f),
          f"a cited suite that FAILS must produce a finding naming the lesson and the file; "
          f"got {f!r}")
    check(not any("L-092" in s for s in f),
          f"a cited suite that passes must NOT be reported (negative control: an "
          f"always-report implementation passes the assertion above); got {f!r}")


def test_main_reports_and_exits_nonzero_on_a_dead_citation():
    mod = load()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "scripts" / "tests").mkdir(parents=True)
        (root / "scripts" / "tests" / "test_red.py").write_text("raise SystemExit(1)\n")
        lessons_file = root / "docs" / "LESSONS.md"
        lessons_file.parent.mkdir(parents=True)
        lessons_file.write_text(
            "**L-091 [VERIFIED 2026-08-25] the forged note.**\n\n"
            "body text.\n\n"
            "ENFORCEMENT: `scripts/tests/test_red.py` asserts the refusal.\n\n")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"))
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--root", str(root),
             "--out", str(root / "workspace" / "lesson-enforcement.jsonl"), "--fast"],
            capture_output=True, text=True, timeout=TIMEOUT_S, env=env)
        check(proc.returncode != 0,
              f"main must exit non-zero when a cited suite fails -- a gate that reports and "
              f"exits 0 is documentation, not a gate. stdout={proc.stdout[-400:]!r} "
              f"stderr={proc.stderr[-400:]!r}")
        check("L-091" in proc.stdout and "test_red.py" in proc.stdout,
              f"the failing lesson and its file must be in the printed report; "
              f"stdout={proc.stdout[-400:]!r}")
        ledger = root / "workspace" / "lesson-enforcement.jsonl"
        check(ledger.exists() and ledger.read_text().strip(),
              "main must append the run to the ledger even when it reports failures -- "
              "silence in the ledger is the decay signal (RCA 64186aa9dbbe section 5)")


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    ran = 0
    for fn in TESTS:
        ran += 1
        try:
            fn()
        except AssertionError as e:
            FAILS.append(f"{fn.__name__}: {e}")
        except Exception as e:                            # noqa: BLE001
            FAILS.append(f"{fn.__name__}: {type(e).__name__}: {e}")
    for f in FAILS:
        print("FAIL:", f)
    print(f"{ran - len({f.split(':')[0] for f in FAILS})}/{ran} checks pass")
    sys.exit(1 if FAILS else 0)
