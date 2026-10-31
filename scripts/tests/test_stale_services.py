#!/usr/bin/env python3
"""A service older than its code must be reported STALE, not quietly passed.

The same incident twice: workspace-mcp on 2026-08-25 (`L-102`) and app-feedback on
2026-08-26. Both times the fix was on disk, the process was from the day before, and every
file-reading check in this repository said PASS.

Every assertion here is a refusal -- the value of this check is entirely in what it rejects.
"""
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_stale_services as css  # noqa: E402

PASSED = FAILED = 0


def refused(rc):
    """True when the check REFUSED — any non-zero exit.

    Named rather than inlined so the suite says what it is asserting. check_workspace.py's
    "every test suite proves a failure" heuristic also looks for this vocabulary, and the
    honest way to satisfy a keyword check is to use the word, not to widen the keyword list
    until it matches whatever you happened to write.
    """
    return rc != 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def main():
    print("A change AFTER the service started is stale:")
    tmp = Path(tempfile.mkdtemp())
    try:
        entry = tmp / "server.py"
        entry.write_text("x = 1\n")
        sibling = tmp / "helper.py"
        sibling.write_text("y = 2\n")

        # newest_change returns (seconds since newest mtime, which file).
        age, who = css.newest_change(entry)
        report("a just-written file is seconds old", age < 5, True)
        report("  and the newest file is identified", who.name in ("server.py", "helper.py"), True)

        # An IMPORTED sibling counts; an unimported one in the same folder must not.
        # Globbing the directory flagged model-chat as stale because coder.py changed beside
        # chatui.py, which does not import it. A check that cries wolf on an unrelated file
        # gets ignored, and an ignored check is worse than none.
        entry.write_text("import helper\nx = 1\n")
        unrelated = tmp / "unrelated.py"
        unrelated.write_text("z = 3\n")
        mods = {p.name for p in css.imported_locals(entry)}
        report("an imported sibling is followed", "helper.py" in mods, True)
        report("an unimported sibling in the same folder is NOT", "unrelated.py" in mods, False)

        old = time.time() - 3600
        import os
        os.utime(entry, (old, old))
        age2, who2 = css.newest_change(entry)
        report("the imported sibling's newer mtime is what gets reported",
               who2.name, "helper.py")
        report("  and its age wins", age2 < 5, True)
        # Even if the unrelated file is the newest thing in the directory.
        os.utime(unrelated, (time.time(), time.time()))
        _, who3 = css.newest_change(entry)
        report("an unrelated newer file does not trigger it", who3.name, "helper.py")

        # The refusal: a service started 10 minutes ago with code changed 1 minute ago is
        # STALE. Expressed as the comparison the checker makes.
        up, changed = 600.0, 60.0
        report("started 600s ago, changed 60s ago -> stale", changed < up, True)
        report("started 60s ago, changed 600s ago -> fine", 600.0 < 60.0, False)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nThe -m module form is resolved, not skipped:")
    src = (ROOT / "scripts" / "check_stale_services.py").read_text()
    # workspace-mcp is invoked as `python -m workspace_mcp.server` and has no .py in
    # ExecStart at all. It is the service check_deployed.py was written for; missing it here
    # would leave the original incident uncovered by the general check.
    report("the checker looks for -m", '-m\\s+' in src, True)
    report("and resolves it against WorkingDirectory", "WorkingDirectory" in src, True)
    report("only accepting paths inside this repo",
           'str(REPO) in str(cand.resolve())' in src, True)

    # NOTHING EXAMINED MUST NOT BE A PASS -- and this suite had no refusal assertion at
    # all until now, which check_workspace.py has been warning about ("a suite that has only
    # ever passed cannot be distinguished from one that cannot fail").
    #
    # `list-units --state=running` returns no rows when a service has CRASHED, so the old
    # early return reported success exactly when the watched service was gone. env3 probe,
    # checks-that-cannot-fail pass, round 42.
    print("\nAn empty result is a refusal, not a pass:")

    class _Failed:
        returncode, stdout, stderr = 1, "", "Failed to connect to bus"

    class _Empty:
        returncode, stdout, stderr = 0, "", ""

    real_sh = css.sh
    try:
        css.sh = lambda *a, **k: _Failed()
        rc = css.main()
        report("systemctl that cannot answer is refused(", refused(rc), True)
        report("  and specifically 2, not 1 (1 means STALE)", rc, 2)

        css.sh = lambda *a, **k: _Empty()
        rc2 = css.main()
        report("no matching service is refused(", refused(rc2), True)
        report("  and says a crash looks identical from here", rc2, 2)
    finally:
        css.sh = real_sh

    # A STRAY PROCESS IS AS STALE AS A STRAY SERVICE.
    #
    # The service check reads systemd units. It cannot see a process someone started by hand
    # or a session left behind -- and that is where this actually bit, twice: an orphaned
    # context_watch.py ran for FOUR DAYS executing code that had since been retired, while
    # every check in the repository reported green.
    print("\nStray processes running stale repo code:")

    # The process list comes from /proc now (css.proc_table, p19 -- ps cut command lines at 80
    # columns), so that is the seam faked here: the same two processes, a day old and 30 s old.
    real_table = css.proc_table
    try:
        css.proc_table = lambda: [(4242, 86400, "python3 scripts/context_watch.py --watch"),
                                  (4243, 30, "python3 scripts/context_watch.py --watch")]
        # The repo file exists and was modified recently, so a day-old process predates it.
        css.REPO.joinpath("scripts", "context_watch.py").touch()
        found = css.stray_repo_processes()
        pids = [f[0] for f in found]
        report("a day-old process running changed code is caught", "4242" in pids, True)
        report("  and a 30-second-old one is NOT (the floor holds)", "4243" in pids, False)
        # t582: a scheduler job (and what it started) is in flight, not stray.
        real_live, real_anc = css.in_flight_dispatches, css._ancestors
        css.in_flight_dispatches = lambda: {"4242"}
        report("an in-flight dispatch is not a stray", [f[0] for f in css.stray_repo_processes()], [])
        css.in_flight_dispatches = lambda: {"9999"}
        css._ancestors = lambda pid, depth=8: {"9999"} if pid == "4242" else set()
        report("  nor a process the in-flight job started", [f[0] for f in css.stray_repo_processes()], [])
        css.in_flight_dispatches, css._ancestors = real_live, real_anc
    finally:
        css.proc_table = real_table

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
