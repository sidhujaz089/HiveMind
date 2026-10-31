#!/usr/bin/env python3
"""E1 (L4, 2026-10-27): two coder.py runs in DIFFERENT clones are not twins.

running_twin() exists to stop two copies racing for the same branch and the same GPU. It matched
label + --files + model across all of /proc, so suites sharing a brief (test_coder_branch_not_clobbered,
test_coder_delivers_what_it_verified, test_who_works: all 'mark the inbox README heading', all
--model faketest) refused each other from separate scratch clones once four lanes ran sweeps at once:
'REFUSED: pid N is already running this job (brief-b9e78ebf, model faketest)'. Seven red suites.

A real process is spawned (a sleeping stand-in named coder.py, inside clone A) and the scan is run
from clone B, then from clone A. Nothing is written outside a temp dir."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("coder", REPO / "projects" / "local-inference" / "coder.py")
coder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coder)

ok = fail = 0


def check(name, cond):
    global ok, fail
    print(f"  [{'ok' if cond else 'FAIL'}]   {name}")
    ok += bool(cond); fail += not cond


base = Path(tempfile.mkdtemp())
a, b = base / "clone-a", base / "clone-b"
for c in (a, b):
    c.mkdir()
(a / "coder.py").write_text("import time\ntime.sleep(60)\n")
p = subprocess.Popen([sys.executable, "coder.py", "--task", "t-twin-probe", "--model", "faketest",
                      "--files", "README.md"], cwd=a)
here = os.getcwd()
try:
    time.sleep(0.5)
    os.chdir(b)
    check("a run in ANOTHER clone is not a twin",
          coder.running_twin("t-twin-probe", ["README.md"], "faketest") is None)
    os.chdir(a)
    check("a run in the SAME clone still is",
          coder.running_twin("t-twin-probe", ["README.md"], "faketest") == p.pid)
finally:
    os.chdir(here)
    p.kill()
    p.wait()

print(f"\n{ok} ok, {fail} fail")
sys.exit(1 if fail else 0)
