#!/usr/bin/env python3
"""t899: scripts/tests/test_check_workspace.py must stop asserting the retired census.

Dispatch p287-t296-check-workspace (t296, swift, 2026-10-08) deleted the family-ledger section
census block from scripts/check_workspace.py -- the change Jas authorised on 2026-10-04
("Go ahead with option A") -- and every attempt was NOT ACCEPTED with
"tests fail (1: test_check_workspace.py)". The reason: that suite asserts the census code is
PRESENT, at scripts/tests/test_check_workspace.py:497-541 -- check("a complete ledger passes
the census", find(statuses(r, "check_drifting_claims"), "section census"), "PASS") at :520-521
and its four siblings at :526, :531, :536, :541, plus the comment block at :497-500. The brief
scoped only scripts/check_workspace.py, so the builder could not touch that suite, and the
all-tests-must-pass gate made the deletion unsatisfiable. That coupling is t899; this oracle
pins the neighbour-suite half of the repair.

RED TODAY for the stated reason, verified: grep finds 'census' in that file at exactly eight
lines -- 497, 499, 520, 521, 526, 531, 536, 541 -- all inside the block 497-541, so deleting
that one contiguous block clears the string from the source AND from the labels the suite
prints (the label at :520 is "a complete ledger passes the census"). The suite passes on main
today (exit 0, "62 passed, 0 failed", 1.4 s), so no assertion here is red for an unrelated
reason.

Behaviour, not shape: the suite is RUN (python3 <suite>) and must exit 0 while saying nothing
about the census, and it must keep the checks that are not about the census. A build that
deletes the file, empties it, or strips it to a stub fails the size, helper, neighbour-check
and pass-count assertions -- the anti-vacuity set. The pass count is PARSED from the suite's
own summary line, not stored: today it reports 62, the census block is 5 checks, so a correct
deletion reports 57. The floor is 55.

Temp-free and read-only: it reads one file from the repository and runs one subprocess with
timeout=600. No model, no server, no fixed /tmp path, nothing left running.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "scripts" / "tests" / "test_check_workspace.py"

PASSED = 0
FAILED = 0


def report(name, got, want, detail=""):
    global PASSED, FAILED
    ok = got == want
    if ok:
        PASSED += 1
    else:
        FAILED += 1
    line = "  %s  %s" % ("PASS" if ok else "FAIL", name)
    if not ok:
        line += "   (got %r, want %r)" % (got, want)
        if detail:
            line += "\n      " + detail
    print(line)


def main():
    if not SUITE.is_file():
        report("the suite file still exists", False, True,
               "missing: %s" % SUITE)
        print("\n%d passed, %d failed\n" % (PASSED, FAILED))
        return 1

    src = SUITE.read_text(encoding="utf-8")

    # --- 1. the retired rule is no longer asserted, named, or printed by the harness ------
    # 'census' and not 'section census' so a leftover comment naming the retired rule fails
    # too: lines 497-500 are exactly such a comment. Verified: all eight 'census' occurrences
    # live inside 497-541, so deleting that one block clears the string from the whole file.
    report("test_check_workspace.py names the census no more",
           "census" in src.lower(), False,
           "the block to delete is scripts/tests/test_check_workspace.py:497-541; "
           "today 'census' appears at 497, 499, 520, 521, 526, 531, 536, 541")

    # --- 2. ANTI-VACUITY: it is still the same suite, it still runs, it is still green ----
    # 651 lines today; the verified block is 497-541 (45 lines), so a correct deletion leaves
    # 606. A deleted, emptied, or gutted file cannot clear 550.
    report("  the suite is still substantial", len(src.splitlines()) >= 550, True,
           "%d lines; the block to remove is 497-541" % len(src.splitlines()))

    # The scaffolding the census calls used must survive -- a build that removes the harness
    # along with the assertions is not the change. Verified: def check( at 30, def statuses(
    # at 77, def find( at 87.
    for helper, where in (("def check(", 30), ("def statuses(", 77), ("def find(", 87)):
        report("  it keeps its helper %s" % helper, helper in src, True,
               "defined at scripts/tests/test_check_workspace.py:%d" % where)

    # The rules that share check_drifting_claims with the census must stay written. Verified:
    # check_structure at 553, check_secrets at 579 -- both outside the block.
    for neighbour in ("check_structure", "check_secrets", "check_headroom_waits"):
        report("  its %s checks survive" % neighbour, neighbour in src, True)

    proc = subprocess.run([sys.executable, str(SUITE)],
                          capture_output=True, text=True, timeout=600)
    report("  the suite runs green", proc.returncode, 0,
           "exit=%s\n--- stdout ---\n%s\n--- stderr ---\n%s"
           % (proc.returncode, proc.stdout, proc.stderr))

    out = proc.stdout + proc.stderr
    # The suite ends with "<n> passed, <m> failed" (verified on main: "62 passed, 0 failed").
    # The census block is 5 check() calls, all passing today (62/0), so a correct deletion
    # reports 57. Computed from the run, never stored as an expected answer.
    m = re.search(r"(\d+)\s+passed,\s+(\d+)\s+failed", out)
    report("  its summary reports the checks that remain",
           (int(m.group(1)), int(m.group(2))) if m else None, (57, 0),
           "62 pass today; the block at 497-541 is 5 checks; floor accepted: >= 55 passed, 0 failed")
    if m:
        report("  and no check is lost beyond the five census ones",
               int(m.group(1)) >= 55 and int(m.group(2)) == 0, True,
               "got %s passed / %s failed" % (m.group(1), m.group(2)))

    report("  and its output says nothing about the census",
           "census" in out.lower(), False,
           "stdout/stderr above; the label at 520 is 'a complete ledger passes the census'")

    print("\n%d passed, %d failed\n" % (PASSED, FAILED))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
