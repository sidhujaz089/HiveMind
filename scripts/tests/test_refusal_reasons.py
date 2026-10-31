#!/usr/bin/env python3
"""A refusal is reported with its REASON, and only while the reason still holds (M2, L4).

THE DEFECT THIS GUARDS. `check_workspace` reported "The harness is not refusing to start jobs
(31 chunk(s) refused)" with, for each chunk, only a model name and a date. Two things were wrong:

  1. NO REASON. `dispatch_loop.refusal_storm()` returns (chunk, n, model, at) and the finished
     ledger row for a refusal carries no reason at all -- 692 exit-2 rows, 0 with a `reason`
     field, and `log: null` on the row, so even the log path had to be recovered by pid. The
     check printed a count with no cause, which is exactly what M2 asks for: every refusal
     carries a reason from the declared vocabulary.
  2. THE WRONG SUITE, AT 15 MINUTES A SESSION START. Staleness was tested with
     `parallel_dispatch.target_is_red(chunk's target)` -- a real subprocess running that test
     suite, 600 s timeout each, once per refused chunk. Measured 2026-10-27: 31 chunks, 15m21s
     for `check_workspace.py --quick`, because two of those target suites hang. And the suite
     that decides staleness is not the target: a baseline refusal names an UNDECLARED SUITE IN
     THE BASELINE, a different file. So the check ran the wrong suite slowly.

WHAT IS ASSERTED HERE:

  * `refusal_reason()` reads the reason out of coder.py's own words, and every real refusal
    message has a code in the declared vocabulary -- or is `unrecoverable`, which counts;
  * `live_refusals()` decides "still refused" from the named suite's recorded verdict, the
    ceiling and git's tracked set -- three conditions, the same ones `dispatch_loop.
    main_red_hold()` uses for the same question, and it runs nothing;
  * a refusal whose named suite is green, gone, declared, untracked or unknown is NOT reported
    as live, and says which of those it was;
  * a refusal with no recoverable reason stays counted: unknown is not a clearance (L-015);
  * `red_of` receives `scripts/tests/<suite>` and is the only source of truth about redness --
    the helper never runs a suite.

Temporary logs only, a stub red_of, and a fake ceiling: no suite is executed.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import check_workspace as C  # noqa: E402

fails = []


def ck(label, got, want):
    print(f"  [{'ok' if got == want else 'FAIL'}]    {label} -> {got!r}")
    if got != want:
        fails.append(f"{label}: expected {want!r}, got {got!r}")


def main():
    # ---- 1. THE VOCABULARY, FROM CODER'S OWN MESSAGES --------------------------------
    print("Every refusal coder.py can print has a declared code:")
    ck("baseline: names the suites",
       C.refusal_reason("  REFUSING TO START -- the sandbox baseline is not what is declared:\n"
                        "    undeclared failure: test_slot_events.py\n"
                        "    undeclared failure: test_chat_lane.py\n", "production"),
       ("baseline-undeclared", ["test_slot_events.py", "test_chat_lane.py"]))
    ck("needs-scope: the brief, not the box",
       C.refusal_reason("  NEEDS-SCOPE -- refusing to start: scripts/x.py: no room\n", "production"),
       ("needs-scope", []))
    ck("inference path did not answer",
       C.refusal_reason("  REFUSING TO START -- the inference path did not answer:\n", "production"),
       ("inference-unanswered", []))
    ck("the model server went away mid-run",
       C.refusal_reason("REFUSING: the model server did not answer for 90 s (ReadTimeout)\n",
                        "production"),
       ("inference-unanswered", []))
    ck("a preflight that objected",
       C.refusal_reason("  ==> STOP. Do not dispatch this brief.\n"
                        "    an unrecognised verdict          -> the model's answer is not in the\n",
                        "preflight"),
       ("preflight-objected", []))
    ck("nothing recognisable is unrecoverable, not a clearance",
       C.refusal_reason("some output nobody classified\n", "production"),
       ("unrecoverable", []))
    ck("an empty log is unrecoverable",
       C.refusal_reason("", "production"), ("unrecoverable", []))
    ck("every code is in the declared vocabulary",
       sorted(C.REFUSAL_CODES), sorted({c for c in C.REFUSAL_CODES}))
    for code, _why in C.REFUSAL_CODES.items():
        ck(f"  {code} has a one-line meaning", bool(str(C.REFUSAL_CODES[code]).strip()), True)

    # ---- 2. STILL REFUSED, OR NOT ----------------------------------------------------
    print("\nA refusal counts only while the suite it named still holds the line:")
    with tempfile.TemporaryDirectory(dir=REPO / "workspace") as d:
        d = Path(d)
        (d / "red.log").write_text("  REFUSING TO START -- the sandbox baseline is not what is declared:\n"
                                   "    undeclared failure: test_red_one.py\n")
        (d / "scope.log").write_text("  NEEDS-SCOPE -- refusing to start: scripts/x.py: no room\n")
        rel = lambda n: str((d / n).relative_to(REPO))
        st = lambda pid, log, chunk="p1": {"event": "started", "chunk": chunk, "pid": pid, "log": rel(log)}
        fin = lambda pid, chunk="p1": {"event": "finished", "chunk": chunk, "pid": pid, "exit": 2,
                                       "kind": "production"}
        hist = [st(1, "red.log"), fin(1)]
        storm = [("p1", 3, "swift", "2026-10-24T19:36:57Z")]
        tracked = {"scripts/tests/test_red_one.py"}

        calls = []

        def red(path):
            calls.append(path)
            return red.answer

        red.answer = True
        live, other = C.live_refusals(storm, hist, red, allowed=set(), tracked=tracked)
        ck("a suite still red on main: the chunk is refused", len(live), 1)
        ck("  and it carries its reason", (live[0]["code"], live[0]["named"]),
           ("baseline-undeclared", ["test_red_one.py"]))
        ck("  red_of was asked about the SUITE, not the chunk's target",
           calls, ["scripts/tests/test_red_one.py"])

        red.answer = False
        live, other = C.live_refusals(storm, hist, red, allowed=set(), tracked=tracked)
        ck("the suite is green now: not counted", len(live), 0)
        ck("  and the reason it is not counted is said", other[0]["why"], "the suites it named are green now")

        red.answer = None
        live, other = C.live_refusals(storm, hist, red, allowed=set(), tracked=tracked)
        ck("unknown is not red, so it holds nothing", len(live), 0)

        red.answer = True
        live, other = C.live_refusals(storm, hist, red, allowed={"test_red_one.py"}, tracked=tracked)
        ck("declared in the ceiling since the refusal: not counted", len(live), 0)

        live, other = C.live_refusals(storm, hist, red, allowed=set(), tracked=set())
        ck("untracked is not main, so it holds nothing", len(live), 0)
        ck("  and that is what it says", other[0]["why"], "the suites it named are not tracked")

        # A refusal with no baseline reason cannot be cleared by looking at a baseline.
        hist2 = [st(2, "scope.log"), fin(2)]
        live, other = C.live_refusals([("p1", 1, "swift", "2026-10-22T00:31:05Z")], hist2, red,
                                      allowed=set(), tracked=tracked)
        ck("needs-scope stays counted, with its own code", (len(live), live[0]["code"]),
           (1, "needs-scope"))

        # No log at all: the row carries no reason and no log path. Unknown is counted.
        liv3, oth3 = C.live_refusals([("p1", 1, "swift", "x")],
                                     [{"event": "finished", "chunk": "p1", "pid": 9, "exit": 2,
                                       "kind": "production"}], red, allowed=set(), tracked=tracked)
        ck("an unrecoverable refusal is counted, never quietly dropped",
           (len(liv3), liv3[0]["code"]), (1, "unrecoverable"))

        # THE LOG IS ON THE ROW, AS result_path. Corrected 2026-10-27 after the first version of
        # _newest_refused_run claimed the row carried no log path and went to the started row by pid:
        # dispatch_loop writes result_path on every finished row.
        no_started = [{"event": "finished", "chunk": "p1", "pid": 7, "exit": 2,
                       "kind": "production", "result_path": rel("red.log")}]
        live, other = C.live_refusals(storm, no_started, red, allowed=set(), tracked=tracked)
        ck("the reason is read from result_path, with no started row at all",
           (len(live), live[0]["code"]), (1, "baseline-undeclared"))

        # A later ACCEPTED run clears everything before it (the ledger's own rule).
        hist4 = [st(1, "red.log"), fin(1),
                 {"event": "finished", "chunk": "p1", "pid": 3, "exit": 0, "accepted": True,
                  "kind": "production"}]
        live, other = C.live_refusals(storm, hist4, red, allowed=set(), tracked=tracked)
        ck("a refusal whose chunk has since been accepted is not live", len(live), 0)
        ck("  and the acceptance is said out loud", "accepted" in other[0]["why"], True)

        # A later run that STARTED and failed is not a refusal either: the harness did not refuse.
        hist5 = [st(1, "red.log"), fin(1),
                 {"event": "finished", "chunk": "p1", "pid": 4, "exit": 1, "kind": "production"}]
        live, other = C.live_refusals(storm, hist5, red, allowed=set(), tracked=tracked)
        ck("a later attempt that started is not a refusal", len(live), 0)
        ck("  and it says so with the exit code", "exit 1" in other[0]["why"], True)

    # ---- 3. THE CHECK MUST NOT RUN A SUITE --------------------------------------------
    print("\nNothing in this path executes a test suite:")
    ck("live_refusals takes redness as a function, so it cannot run one",
       "red_of" in C.live_refusals.__code__.co_varnames[:8], True)

    for x in fails:
        print("FAIL:", x)
    print("test_refusal_reasons:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
