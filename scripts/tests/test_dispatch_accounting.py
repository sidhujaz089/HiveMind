#!/usr/bin/env python3
"""Every dispatch is accounted for: started, and terminally recorded. The gap is a finding.

WHY (t275, and the committee's strongest structural note). coder.py DOES call record() when a
run is NOT ACCEPTED -- that path was never the bug. The bug is that on 2026-09-11 three
dispatches were killed by `timeout` mid-run, so the process died before reaching any record()
call, and three more early returns (missing target, undeclared baseline, model exception) exit
without recording at all. Five coder.py runs that day produced TWO ledger rows, both successes.

A LEDGER THAT RECORDS SUCCESSES AND IS SILENT ABOUT FAILURES IS THE EXACT INVERSION OF WHAT A
FAILURE LOG IS FOR. "This chunk has now failed three times" was uncomputable, and had to be
worked out by hand.

THE FIX IS NOT "call record() in more places" -- a killed process cannot call anything. The
row goes down when the dispatch OPENS, and the terminal row closes it. Then the absence of a
close is itself observable, which is the property that matters:

    unaccounted = started - terminally recorded

ChatGPT's review put it exactly: "Can the ledger prove that every execution had a ledger
entry?" is a different and stronger question than "is the ledger reliable?"

`unaccounted()` is PURE -- rows in, ids out -- so this suite drives it with no dispatch, no
sandbox and no model. That is deliberate: the old behaviour was untestable, which is why it
survived.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))

TERMINAL = ("ACCEPTED", "ACCEPTED-DRYRUN", "ACCEPTED-NOT-WRITTEN", "TESTS-FAILED",
            "NO-ATTEMPTS", "REFUSED-NO-TARGET", "REFUSED-BASELINE", "ERROR")


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'BAD'}]    {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(f"{label} {detail}")

    import coder
    check("coder exposes unaccounted()", hasattr(coder, "unaccounted"))
    check("coder exposes open_run()", hasattr(coder, "open_run"))
    if not (hasattr(coder, "unaccounted") and hasattr(coder, "open_run")):
        print("\nFAIL: nothing to test")
        return 1
    ua = coder.unaccounted

    # 1. THE THING IT IS FOR: a dispatch that opened and never closed is a finding, named.
    rows = [{"dispatch_id": "d1", "outcome": "STARTED", "task": "chunk-A"}]
    got = ua(rows)
    check("an opened dispatch with no terminal row is reported", len(got) == 1, str(got))
    check("...and the finding names the dispatch id", "d1" in " ".join(got), str(got))

    # 2. A CLOSED DISPATCH IS SILENT -- or the check fires on every healthy run and is disabled
    #    inside a week (AGENTS.md rule 9), which is worse than not having it.
    for term in TERMINAL:
        rows = [{"dispatch_id": "d2", "outcome": "STARTED"},
                {"dispatch_id": "d2", "outcome": term}]
        check(f"a dispatch closed with {term} is silent", ua(rows) == [], str(ua(rows)))

    # 3. THE MIXED CASE, which no constant can pass: report the open one while staying silent
    #    about the closed one, in the same call.
    rows = [{"dispatch_id": "d3", "outcome": "STARTED"},
            {"dispatch_id": "d3", "outcome": "ACCEPTED"},
            {"dispatch_id": "d4", "outcome": "STARTED"}]
    got = " ".join(ua(rows))
    check("mixed: the unclosed one is reported", "d4" in got, got)
    check("mixed: the closed one is NOT", "d3" not in got, got)

    # 4. A TERMINAL ROW WITH NO START is also a finding. It means a dispatch ran that nothing
    #    opened -- the ledger cannot then prove it saw every execution, which is the point.
    rows = [{"dispatch_id": "d5", "outcome": "ACCEPTED"}]
    got = ua(rows)
    check("a terminal row with no STARTED row is reported", len(got) == 1, str(got))

    # 5. LEGACY ROWS MUST NOT DROWN IT. Rows predating dispatch_id are ignored, not reported --
    #    127 of them exist. If they surfaced, the real findings would be lost in the noise and
    #    the check would be turned off.
    rows = [{"at": "2026-09-01", "outcome": "ACCEPTED", "task": "old"},
            {"dispatch_id": "d6", "outcome": "STARTED"}]
    got = ua(rows)
    check("rows with no dispatch_id are ignored", len(got) == 1 and "d6" in " ".join(got),
          str(got))

    # 6. UNREADABLE IS NOT ABSENT. A row that cannot be understood must not be silently
    #    dropped -- that is L-146, and it is the defect this whole task belongs to.
    rows = [{"dispatch_id": "d7"}]          # no outcome at all
    got = ua(rows)
    check("a row with no outcome is reported, not skipped", len(got) >= 1, str(got))

    # 7. open_run WRITES A ROW IMMEDIATELY, because a killed process cannot write one later.
    #    This is the whole reason the fix is not "call record() in more places".
    import inspect
    src = inspect.getsource(coder.open_run)
    check("open_run writes STARTED", "STARTED" in src)
    check("open_run returns an id the terminal row can match",
          "return" in src and "dispatch_id" in src)

    print()
    if fails:
        print(f"FAIL: {len(fails)} problem(s)")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
