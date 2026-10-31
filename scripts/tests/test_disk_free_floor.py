#!/usr/bin/env python3
"""check_workspace says when the disk is filling (t9566, Jas 2026-10-29: "Add it").

The SSD reached 98% -- 18 GB free -- with no check saying so. A fake statvfs sets the free space; the check must
PASS above the warn floor, WARN between the floors, FAIL under the fail floor, and name the reap command.
"""
import contextlib
import io
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as CW  # noqa: E402

FAILED = 0


def check(name, ok, detail=""):
    global FAILED
    FAILED += not ok
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))


def run(free_gb):
    c = CW.Checker(ROOT, quick=True)
    c.passes = c.warnings = c.failures = 0
    fake = lambda _p: SimpleNamespace(f_bavail=int(free_gb * 2**30 / 4096), f_frsize=4096,
                                      f_blocks=int(916 * 2**30 / 4096))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        c.check_disk_free(path="/", statvfs=fake, worktrees=207)
    return c, buf.getvalue()


c, out = run(246)
check("246 GB free passes", c.passes == 1 and not c.warnings and not c.failures, out)
c, out = run(80)
check("80 GB free WARNS (under the 100 GB floor)", c.warnings == 1 and not c.failures, out)
c, out = run(18)
check("18 GB free -- the 2026-10-29 state -- FAILS", c.failures == 1, out)
check("  and names the reap command and the worktree count", "sweep_tmp_repo_copies" in out and "207" in out, out)
check("the check is wired into the run", "(self.check_disk_free, ())" in (ROOT / "scripts" / "check_workspace.py").read_text())
sys.exit(1 if FAILED else 0)
