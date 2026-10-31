#!/usr/bin/env python3
"""A check must only FAIL on what its own remedy can actually fix (2026-10-30).

Found by running the remedy. check_workspace reported "3 repository cop(y/ies) in RAM-backed /tmp ... Reap with:
sweep.sweep_tmp_repo_copies('/tmp', max_age_s=3600)". The command returned [] and the copies stayed -- because they
were MINUTES old, and the reaper deliberately skips anything younger than max_age_s (a running test's clone must not
be deleted under it). The check that fails on a 5-minute-old copy and then points at a command that will not touch it
is a check whose advice cannot work, and that is how a red main gets ignored.

The rule now lives in one place: copies OLDER than the reap threshold are the failure (the remedy can take them);
YOUNGER ones are a warning, because the next reaper run or the hourly sweep will.

RED ON MAIN: sweep has no classify_tmp_copies.
"""
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import sweep          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


now = 1_000_000.0
copies = [("/tmp/old-1", 10.0, now - 7200), ("/tmp/old-2", 3.0, now - 4000),
          ("/tmp/young-1", 5.0, now - 60), ("/tmp/young-2", 1.0, now - 3599)]
stale, recent = sweep.classify_tmp_copies(copies, now=now, reap_after_s=3600)
check("a copy older than the reap threshold is STALE, so the check may fail on it",
      sorted(p for p, _ in stale) == ["/tmp/old-1", "/tmp/old-2"], str(stale))
check("a copy younger than it is RECENT, and only warns",
      sorted(p for p, _ in recent) == ["/tmp/young-1", "/tmp/young-2"], str(recent))
check("the two lists partition the input", len(stale) + len(recent) == len(copies), f"{stale} / {recent}")
check("sizes come through unchanged", dict((p, s) for p, s in stale)["/tmp/old-1"] == 10.0, str(stale))
check("nothing at all is two empty lists, never a guess",
      sweep.classify_tmp_copies([], now=now, reap_after_s=3600) == ([], []))

src = (HERE / "scripts" / "check_workspace.py").read_text(encoding="utf-8")
check("check_workspace asks that one function", "classify_tmp_copies" in src)
check("  and its FAIL is the stale half only", "stale" in src and "recent" in src)
# THE GREP ABOVE PASSED WHILE THE CHECK WOULD HAVE RAISED NameError, the same way SCRIPTS did. Ask the module, not
# the text: the helper the wiring calls must EXIST and answer with a number.
import importlib
cw = importlib.import_module("check_workspace")
check("  and the helper its wiring calls exists", hasattr(cw, "_mtime_for"), "_mtime_for")
check("  and it answers with a number for a real path",
      isinstance(cw._mtime_for(Path("/tmp")), float), "_mtime_for('/tmp')")

print("test_tmp_copy_ages:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
