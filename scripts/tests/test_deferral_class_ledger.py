#!/usr/bin/env python3
"""The deferral-misclassification class's own fixes are open and untracked (t1431).

Detection works every time; the fixes are open and every one is blocked at the build line,
and nothing reconciles 'this class has N open specified fixes, all blocked'. This suite pins
the ledger that does that reconciliation: one machine-readable row per fix, a target suite run
to decide whether the fix landed, and a non-zero verdict while open >= 1 and landed == 0.

Run: python3 scripts/tests/test_deferral_class_ledger.py   (pytest is not installed)
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> the repo root
MODULE_PATH = ROOT / "scripts" / "check_deferral_class.py"
TIMEOUT_S = 60
LEDGER = "workspace/ledgers/deferral-class-blockers.jsonl"

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def load():
    if not MODULE_PATH.exists():
        raise AssertionError(
            f"{MODULE_PATH} does not exist. The blocked-fix ledger for the "
            f"deferral-misclassification class has not been written (t1431).")
    spec = importlib.util.spec_from_file_location("check_deferral_class", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- planted fixtures, in the REAL shape of workspace/tasks/*.md (frontmatter + ## Notes),
#     copied from the two task files read this session (t1362.md, t1431.md).
FIX_OPEN = """---
id: t1019
title: Add a resolution scan for deferral dispositions
area: workspace
kind: unfiled
status: queued
assigned: production
source: session-rca
created: 2026-10-18T083001Z
---

## Notes

**2026-10-18 · manager** — (Manager - swift) blocked: the deferral disposition cannot be
discharged in-session; the target suite `scripts/tests/test_t1019_resolution_scan.py` is
red on main and the sandbox baseline refuses to start.
"""

FIX_NOT_IN_CLASS = """---
id: t1444
title: Split check_workspace.py into scripts/checks/
area: workspace
kind: unfiled
status: queued
assigned: production
source: session-rca
created: 2026-10-18T110114Z
---

## Notes

**2026-10-18 · claude-code** — (Claude - Code) Reassigned claude -> production.
"""


def tree(root: Path, suites: dict) -> Path:
    """A planted repository: the two task files above, plus target suites with chosen exit codes."""
    (root / "workspace" / "tasks").mkdir(parents=True, exist_ok=True)
    (root / "workspace" / "tasks" / "t1019--resolution-scan.md").write_text(FIX_OPEN, encoding="utf-8")
    (root / "workspace" / "tasks" / "t1444--split.md").write_text(FIX_NOT_IN_CLASS, encoding="utf-8")
    (root / "scripts" / "tests").mkdir(parents=True, exist_ok=True)
    for rel, code in suites.items():
        (root / rel).write_text(f"import sys\nraise SystemExit({code})\n", encoding="utf-8")
    return root


def rows_of(mod, root):
    return mod.scan(root)


def test_scan_returns_one_row_per_open_fix_and_none_for_others():
    mod = load()
    with tempfile.TemporaryDirectory() as td:
        root = tree(Path(td), {"scripts/tests/test_t1019_resolution_scan.py": 1})
        rows = rows_of(mod, root)
    check(isinstance(rows, list) and len(rows) == 1,
          f"the class has exactly one planted open fix; scan() must return exactly one row, "
          f"and must not report the task with no deferral language in it. Got {rows!r}")
    if not rows:
        return
    r = rows[0]
    check(set(r) >= {"task", "target", "blocker", "landed", "ts"},
          f"row missing contract keys: {sorted(r)!r}")
    check(r["task"] == "t1019", f"the row must name the fix's task id, got {r!r}")
    check(r["target"] == "scripts/tests/test_t1019_resolution_scan.py",
          f"the row must name the fix's target suite, parsed from its record, got {r!r}")
    check("red on main" in str(r["blocker"]),
          f"the row must carry the RECORDED blocker text ('red on main'), not a placeholder; "
          f"got {r['blocker']!r}")


def test_open_and_blocked_produces_a_finding_naming_the_fix():
    mod = load()
    with tempfile.TemporaryDirectory() as td:
        root = tree(Path(td), {"scripts/tests/test_t1019_resolution_scan.py": 1})
        rows = rows_of(mod, root)
    if not rows:
        return
    check(rows[0]["landed"] is False,
          f"a fix whose target suite exits 1 is NOT landed; got {rows[0]!r}")
    f = mod.findings(rows)
    check(any("t1019" in s for s in f),
          f"an open fix that has not landed must produce a finding naming the task id; got {f!r}")


def test_a_fix_lands_only_when_its_target_suite_is_green():
    # The RCA's own acceptance test, and the negative control that kills an
    # always-red implementation: same tree, target suite exits 0.
    mod = load()
    with tempfile.TemporaryDirectory() as td:
        root = tree(Path(td), {"scripts/tests/test_t1019_resolution_scan.py": 0})
        rows = rows_of(mod, root)
    if not rows:
        return
    check(rows[0]["landed"] is True,
          f"a fix whose target suite exits 0 IS landed; got {rows[0]!r}")
    f = mod.findings(rows)
    check(not any("t1019" in s for s in f),
          f"a landed fix must NOT be reported (negative control: an always-report "
          f"implementation passes the assertion above); got {f!r}")


def test_ledger_rows_are_appended_and_valid_json():
    mod = load()
    with tempfile.TemporaryDirectory() as td:
        root = tree(Path(td), {"scripts/tests/test_t1019_resolution_scan.py": 1})
        out = root / "workspace" / "ledgers" / "deferral-class-blockers.jsonl"
        rows = mod.collect(root=root, out=out)
        check(len(rows) == 1, f"one row per open fix; got {rows!r}")
        check(out.exists(), f"no ledger written at {out}")
        lines = out.read_text().splitlines()
        check(len(lines) == 1, f"expected 1 jsonl line, got {len(lines)}")
        json.loads(lines[0])
        mod.collect(root=root, out=out)
        check(len(out.read_text().splitlines()) == 2,
              "the second run must APPEND to the ledger, not rewrite it")


def test_main_is_red_while_the_class_has_open_fixes_and_none_landed():
    mod = load()
    with tempfile.TemporaryDirectory() as td:
        root = tree(Path(td), {"scripts/tests/test_t1019_resolution_scan.py": 1})
        env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"))
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--root", str(root),
             "--out", str(root / "workspace" / "ledgers" / "deferral-class-blockers.jsonl")],
            capture_output=True, text=True, timeout=TIMEOUT_S, env=env)
        check(proc.returncode != 0,
              f"main must exit non-zero when the class has >=1 open fix and zero landed -- "
              f"a ledger that reports and exits 0 is documentation, not a gate. "
              f"stdout={proc.stdout[-400:]!r} stderr={proc.stderr[-400:]!r}")
        check("t1019" in proc.stdout,
              f"the printed report must name the open fix; stdout={proc.stdout[-400:]!r}")
        ledger = root / "workspace" / "ledgers" / "deferral-class-blockers.jsonl"
        check(ledger.exists() and ledger.read_text().strip(),
              "main must write the ledger even on a red run -- silence in the ledger is the "
              "decay signal for this class's own fixes")


def test_an_empty_class_is_not_a_finding():
    # Negative control the other way: with no fix of the class on disk, the gate is silent.
    # Without this assertion an always-red implementation passes the tests above.
    mod = load()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "workspace" / "tasks").mkdir(parents=True)
        (root / "workspace" / "tasks" / "t1444--split.md").write_text(
            FIX_NOT_IN_CLASS, encoding="utf-8")
        rows = rows_of(mod, root)
        env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"))
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--root", str(root),
             "--out", str(root / "workspace" / "ledgers" / "x.jsonl")],
            capture_output=True, text=True, timeout=TIMEOUT_S, env=env)
    check(rows == [], f"a task with no deferral language in it is not in the class; got {rows!r}")
    check(proc.returncode == 0,
          f"an empty class must exit 0 -- a gate that fires on nothing is the 'correct and "
          f"ignored' shape; exit={proc.returncode} stdout={proc.stdout[-300:]!r}")


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
