#!/usr/bin/env python3
"""t731 -- scripts/agent_status.py --self-test must be hermetic.

self_test() plants a dead coder-* sandbox in the SHARED temp dir (agent_status.py:296)
and then asserts, at agent_status.py:307-311, that removing it clears the disagreement
-- a claim about the host, not about the tool. So --self-test is RED exactly when a live
coder.py run has left a sandbox behind, and GREEN only when no such sandbox exists.
Source: workspace/rca/bef22a9c14fc.md ("pre-existing (a live coder run makes its second
check false)"), task t731.

RED ON MAIN, for that reason: test_self_test_survives_an_ambient_orphan fails on its first
assertion (rc=1: main's second check reads the ambient dir and prints [FAIL]) and
test_ambient_population_is_named_and_counted fails because main prints no population line.
The other two pass on main and guard against a fix that deletes the check instead of
scoping it. Run: python3 scripts/tests/test_agent_status_self_test_hermetic.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
SCRIPT = REPO / "scripts" / "agent_status.py"
TIMEOUT_S = 120
# A pid above the box's pid_max: no live process can own this name, so a coder-* dir
# carrying it is an orphan by definition (parsed at agent_status.py:147, used at 158).
DEAD_PID = "999999"
AMBIENT = "coder-1790000000-999999"


def _run_self_test(tmpdir):
    return subprocess.run([sys.executable, str(SCRIPT), "--self-test"],
                          capture_output=True, text=True, timeout=TIMEOUT_S,
                          env={**os.environ, "TMPDIR": str(tmpdir)})


def test_self_test_survives_an_ambient_orphan():
    """A sandbox the self-test did not create must not decide its verdict."""
    d = Path(tempfile.mkdtemp())
    try:
        (d / AMBIENT).mkdir()
        r = _run_self_test(d)
        assert r.returncode == 0, (
            f"--self-test failed because of a sandbox it did not create (rc={r.returncode})\n"
            f"--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}")
        assert "FAIL" not in r.stdout, (
            f"--self-test judged ambient state pass/fail; it must count it:\n{r.stdout}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_ambient_population_is_named_and_counted():
    """The report names what it examined: a line with 'ambient', the count, the name.
    (t647/t658: a flat count over a hidden sub-population is not a report.)"""
    d = Path(tempfile.mkdtemp())
    try:
        (d / AMBIENT).mkdir()
        r = _run_self_test(d)
        lines = [l for l in r.stdout.splitlines() if "ambient" in l.lower()]
        assert lines, f"no output line names the ambient sandbox population:\n{r.stdout}"
        named = lines[0]
        assert AMBIENT in named, f"the population line does not name the dir examined:\n{named}"
        assert re.search(r"(?<!\d)1(?!\d)", named), \
            f"the population line gives no count of 1:\n{named}"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_self_test_leaves_no_fixture_behind():
    """Hermetic teardown: everything it created under TMPDIR is gone when it exits."""
    d = Path(tempfile.mkdtemp())
    try:
        (d / AMBIENT).mkdir()
        r = _run_self_test(d)
        leftovers = sorted(str(p.relative_to(d)) for p in d.rglob("coder-*")
                           if p.is_dir() and p.name != AMBIENT)
        assert not leftovers, (
            f"--self-test left fixture(s) under TMPDIR: {leftovers} "
            f"(rc={r.returncode})\n{r.stdout}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_the_planted_sandbox_is_the_one_reported():
    """The fabricated disagreement is real and is its OWN fixture: it must name a
    coder-* dir whose timestamp is not the ambient one."""
    d = Path(tempfile.mkdtemp())
    try:
        (d / AMBIENT).mkdir()
        r = _run_self_test(d)
        disc = [l for l in r.stdout.splitlines() if "DISAGREEMENT" in l]
        assert disc, f"no DISAGREEMENT is reported for the fixture it plants:\n{r.stdout}"
        names = set(re.findall(r"coder-\d+-\d+", disc[0]))
        assert names, f"the disagreement names no sandbox dir:\n{disc[0]}"
        assert names - {AMBIENT}, (
            f"the reported sandbox is the ambient dir, not a fixture of its own: "
            f"{sorted(names)}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
                fails.append(name)
            except Exception as e:
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                fails.append(name)
    print("\nall green" if not fails else f"\n{len(fails)} failure(s)")
    sys.exit(1 if fails else 0)
