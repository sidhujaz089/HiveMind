#!/usr/bin/env python3
"""build_procs must mean BUILD, not "a process running coder.py somewhere" (A5 follow-up, 2026-10-30).

The sampler grouped any process whose cmdline contains `/coder.py` as job:build, and the coder SUITES run
coder.py as a fixture. Measured on this box: hundreds of pids in build_procs joined to no dispatch row, and three
alive ones traced to

  /home/user/.cache/hivemind-tmp/run-tests-test_coder_branch_delivery-m9we2248/coder-1793379

-- a suite clone. So the field that exists to measure what a BUILD spends was mostly counting tests, and my own gate
runs were feeding it.

The reader was never wrong (only pids with a `started` row are counted), but a field that overstates what is
building is a field nobody can quote, and the next person to trust it would inherit the error.

RED ON MAIN: memory_watch has no _is_build_proc.
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import memory_watch as mw          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


real = f"{HERE}/projects/local-inference/coder.py --brief-file /x --model swift --apply"
clone = "/home/user/.cache/hivemind-tmp/run-tests-test_coder_branch_delivery-m9we2248/coder-1793379"
other = f"{HERE}/scripts/preflight_brief.py --brief /x"

check("a dispatched coder.py IS a build", mw._is_build_proc(real) is True, real)
check("a coder.py inside a test clone is NOT", mw._is_build_proc(clone) is False, clone)
check("  and a run-tests directory is not a build wherever it lives",
      mw._is_build_proc("/tmp/run-tests-foo/coder-1") is False, "/tmp/run-tests-foo/coder-1")
check("a different job script is not a build", mw._is_build_proc(other) is False, other)
check("an empty cmdline is not a build", mw._is_build_proc("") is False, "")
check("the real path is what decides, not the file name alone",
      mw._is_build_proc("python3 /elsewhere/coder.py --apply") is False, "/elsewhere/coder.py")

src = (HERE / "scripts" / "memory_watch.py").read_text(encoding="utf-8")
check("the sampler uses it where it labels build pids", "_is_build_proc(" in src and
      'group_of(pid) == "job:build"' in src, "the filter must sit in sample()")

print("test_build_proc_filter:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
