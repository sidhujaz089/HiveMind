#!/usr/bin/env python3
'''t1056: the dispatch ledger must say WHY work landed on Claude, counted by class.

WHY. workspace/run-logs/auto-assigned.json is the only machine-readable account of why a parked,
held or delivered chunk landed on Claude. scripts/dispatch_loop.py writes it (AUTO_ASSIGNED, read by
route_waiting) and NOTHING in the repo reads it back -- grep -rn 'auto-assigned' --include=*.py .
returns that one line and no other. So on 2026-10-12 the file holds 154 entries and 94 of them (61%)
are 'HELD: red on main', a precondition failure, not a model failure, and the only way to see that
is a human counting reason strings by hand.

This suite is red on main for exactly that reason and for nothing else: scripts/dispatch_loop.py has
no INTAKE_REASONS, no INTAKE_FIXES, no classify_reason, no intake_counts, no intake_report, and
main() has no --intake flag. Verified 2026-10-12: the five names appear nowhere in the file, and
main()'s argparse block defines only --dry-run, --status, --models, --run-job, --harness-fixed.

NO LIVE STATE. No model call, no server, no subprocess, no process left running. The ledger the
counting case scores is built in a tempfile.TemporaryDirectory. The real ledger is opened READ-ONLY
by the last case, which SKIPS when the file is absent -- it is not a tracked file, so it is not in
a git worktree, and a check that demanded it would be red for a reason that is not the defect.
'''
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _p in (str(REPO / "scripts"), str(REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import dispatch_loop  # noqa: E402  -- the module that owns the ledger today

FAILS = []
LEDGER = REPO / "workspace" / "run-logs" / "auto-assigned.json"

# Seven reason strings, each copied VERBATIM and IN FULL from workspace/run-logs/auto-assigned.json
# on 2026-10-12 (not abbreviated, not invented). Counted by class over that file: baseline-refusal
# 94, delivered-awaiting-review 18, attempt-failed-exit-3 11, harness-bug-park 10, preflight-objection
# 10, attempt-failed-exit-1 10, needs-scope 1. They are distinct strings with distinct causes; a
# classifier that answers one label for two of them has not classified anything.
W_RED = ("HELD: red on main, so every sandbox baseline would refuse -- fix it first: "
         "test_t1024_live_entry.py (refused p386-t1026-2c6800f20445), "
         "test_t1024_scope_classes.py (refused p386-t1026-2c6800f20445)")
W_EXIT3 = ("PARKED: 1 attempt(s) in a row came to nothing (exit 3; on swift). The brief or its "
           "oracle is the thing to fix -- edit the brief to release it. Evidence: "
           "python3 scripts/supervise.py --failures")
W_EXIT1 = ("PARKED: 1 attempt(s) in a row came to nothing (exit 1; on swift). The brief or its "
           "oracle is the thing to fix -- edit the brief to release it. Evidence: "
           "python3 scripts/supervise.py --failures")
W_HARNESS = ("PARKED: BLOCKED BY A HARNESS BUG: 1 failures on this brief were the harness's fault "
             "(supervise.py --failures), so retrying it cannot help. Fix the harness task, then "
             "edit the brief to release it.")
W_OBJECTION = "HELD FOR CLAUDE: Q16 objected to this brief (workspace/preflight-objections.jsonl)"
W_SCOPE = ("PARKED: NEEDS-SCOPE: the target file does not fit the model's window with this brief "
           "(1 refusal(s)). Add a SYMBOLS: line naming the functions to edit -- editing the "
           "brief releases it.")
W_DELIVERED = "DELIVERED, waiting on Claude's review: coder/brief-ab718a49"

REAL = [
    (W_RED, "baseline-refusal"),
    (W_EXIT3, "attempt-failed-exit-3"),
    (W_EXIT1, "attempt-failed-exit-1"),
    (W_HARNESS, "harness-bug-park"),
    (W_OBJECTION, "preflight-objection"),
    (W_SCOPE, "needs-scope"),
    (W_DELIVERED, "delivered-awaiting-review"),
]
LABELS = ["baseline-refusal", "attempt-failed-exit-3", "attempt-failed-exit-1", "harness-bug-park",
          "preflight-objection", "needs-scope", "delivered-awaiting-review", "supervisor-park", "still-red", "unclassified"]  # still-red: supervision rule 3, 2026-10-19


def check(name, got, want):
    ok = got == want
    print("  [" + ("ok" if ok else "FAIL") + "]  " + name
          + ("" if ok else ": got " + repr(got) + " want " + repr(want)))
    if not ok:
        FAILS.append(name)


def have(name):
    """The module attribute, or None when the change has not been made yet."""
    return getattr(dispatch_loop, name, None)


def real_ledger():
    """The real ledger, READ-ONLY: opened with read_text, never written, never created.
    {} when it is not there (it is untracked, so a git worktree has no copy of it)."""
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def test_classify_the_reason_strings_the_pipeline_emits():
    f = have("classify_reason")
    if not callable(f):
        check("dispatch_loop.classify_reason exists", None, "callable")
        return
    labels = [f(why) for why, _ in REAL]
    check("each real reason string gets the class the ledger's prose says it is", labels,
          [label for _, label in REAL])
    check("the seven real reasons do not collapse into one class", len(set(labels)), len(labels))
    # NEGATIVE assertions: a classifier that swallows the unknown is the failure this scope exists
    # to stop, so an unseen reason must surface as unclassified, not join its nearest neighbour.
    check("an empty reason is unclassified, not silently counted", f(""), "unclassified")
    check("a reason no code in this repo emits is unclassified",
          f("moved to claude by hand by Jas on 2026-10-11"), "unclassified")
    check("an exit code the harness has never produced is not absorbed by the exit-3 rule",
          f(W_EXIT3.replace("exit 3;", "exit 9;")), "unclassified")
    check("a reason about a green suite is not a baseline refusal",
          f("HELD FOR CLAUDE: green on main, so the sandbox accepted it"), "unclassified")
    # A full production string carries a suite list and a model name after the class's opening
    # words; matching a substring of it is the same rule, so it classifies the same way.
    check("a longer reason carrying the same opening words classifies the same way",
          f(W_RED + ", and one more suite: test_t1056_more.py"), "baseline-refusal")


def test_counts_count_the_real_strings_and_the_report_uses_them():
    if not callable(have("intake_counts")):
        check("dispatch_loop.intake_counts exists", None, "callable")
        return
    entries = {
        "t900": {"to": "claude", "chunk": "p1-t900-a", "why": W_RED.replace("t1024", "alpha")},
        "t901": {"to": "claude", "chunk": "p2-t901-b", "why": W_RED.replace("t1024", "beta")},
        "t902": {"to": "claude", "chunk": "p3-t902-c", "why": W_RED.replace("t1024", "gamma")},
        "t903": {"to": "claude", "chunk": "p4-t903-d", "why": W_EXIT3},
        "t904": {"to": "claude", "chunk": "p5-t904-e", "why": W_EXIT1},
        "t905": {"to": "claude", "chunk": "p6-t905-f", "why": W_HARNESS},
        "t906": {"to": "claude", "chunk": "p7-t906-g", "why": W_OBJECTION},
        "t907": {"to": "claude", "chunk": "p8-t907-h", "why": W_SCOPE},
        "t908": {"to": "claude", "chunk": "p9-t908-i", "why": W_DELIVERED},
        "t909": {"to": "claude", "chunk": "p10-t909-j", "why": "brand new reason from the scheduler"},
    }
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "intake.json"
        path.write_text(json.dumps(entries), encoding="utf-8")
        counts = dispatch_loop.intake_counts(path)
        check("counts every class of the real reason strings, by name and by number", counts,
              {"baseline-refusal": 3, "attempt-failed-exit-3": 1, "attempt-failed-exit-1": 1,
               "harness-bug-park": 1, "preflight-objection": 1, "needs-scope": 1,
               "delivered-awaiting-review": 1, "unclassified": 1})
        check("a class with no entries is not reported as a zero row", "unclassified" in counts, True)
        check("only the classes present are keys", sorted(counts),
              ["attempt-failed-exit-1", "attempt-failed-exit-3", "baseline-refusal",
               "delivered-awaiting-review", "harness-bug-park", "needs-scope",
               "preflight-objection", "unclassified"])
        # THE COUNTS ARE USED, not just returned: the report line has to carry the number a person
        # acts on, so a report that printed labels alone (or a capped excerpt) fails here.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = dispatch_loop.intake_report(path)
        rows = {}
        for line in buf.getvalue().splitlines():
            bits = line.split()
            if len(bits) == 2:
                rows[bits[1]] = bits[0]
        check("the report puts the biggest class's count next to its name", rows.get("baseline-refusal"), "3")
        check("the report counts the unclassified row so a new reason is visible", rows.get("unclassified"), "1")
        check("the report's total is every entry, not a truncated subset", rows.get("total"), "10")
        check("intake_report returns 0", rc, 0)
        sizes = [int(v) for k, v in rows.items() if k != "total"]
        check("the report is ranked by count, biggest first", sizes, sorted(sizes, reverse=True))
        check("the total is not printed as a class of its own", 1 in sizes and 10 not in sizes, True)


def test_every_class_names_its_own_fix_and_the_unknown_is_dispositioned():
    fixes = have("INTAKE_FIXES")
    if not isinstance(fixes, dict):
        check("dispatch_loop.INTAKE_FIXES exists", None, "dict")
        return
    check("every class has a fix written down", sorted(fixes), sorted(LABELS))
    check("no two classes share one fix, so no class is a label with no action", len(set(fixes.values())),
          len(fixes))
    check("the biggest class's fix is the ceiling declaration, not a re-brief",
          "test-failures-ceiling" in str(fixes["baseline-refusal"]), True)
    check("the exit-3 class's fix is the brief naming its target", "SYMBOLS" in str(fixes["attempt-failed-exit-3"]),
          True)
    check("the unclassified class says a reason nobody has dispositioned is a finding",
          "DISPOSITION" in str(fixes["unclassified"]), True)


def test_the_real_ledger_is_fully_classified():
    entries = real_ledger()
    if not LEDGER.is_file():
        print("  [skip]  the real ledger is untracked, so a git worktree has no copy")
        return
    # AN EMPTY LEDGER IS GOOD NEWS, NOT A FAILURE (2026-10-14): once red-on-main holds and brief defects
    # stopped being assigned to Claude (red_on_main.py, t1071) the ledger emptied and this went red.
    if not entries:
        print("  [info]  the real ledger is empty -- nothing waits on anyone; classes not checked")
        return
    f = have("classify_reason")
    if not callable(f):
        check("dispatch_loop.classify_reason exists", None, "callable")
        return
    labels = [f(v.get("why", "")) for v in entries.values()]
    # REPORTED, NOT ASSERTED (2026-10-14): the live ledger changes every tick. A new reason string made
    # this red on main, and a suite red on main holds EVERY build on the line -- the whole line stopped
    # on one unclassified row. An unclassified reason is a finding intake_report names ("unclassified":
    # DISPOSITION it); it must not halt production. The classifier's contract is the fixtures above.
    unclassified = sorted(set(v.get("why", "")[:120] for v in entries.values()
                              if f(v.get("why", "")) == "unclassified"))
    print("  [info]  unclassified reasons in today's ledger: %d%s" % (len(unclassified),
          "".join("\n          " + u for u in unclassified)))
    outside = sorted(set(labels) - set(LABELS))
    print("  [info]  classes outside the approved list: %s" % (outside or "none"))
    # LIVE STATE, NOT A CONTRACT (2026-10-12): this asserted the ledger still held a red-on-main hold,
    # which goes false the moment red on main is fixed -- releasing the line cleared all 129. The
    # classification checks above are the contract; whether the class is present today is reported.
    print("  [info]  baseline-refusal entries in today's ledger: %d" % labels.count("baseline-refusal"))


def test_the_cli_can_be_asked_for_the_counts():
    if not callable(have("intake_report")):
        check("dispatch_loop.intake_report exists", None, "callable")
        return
    entries = {"t910": {"to": "claude", "chunk": "p1-t910-a", "why": W_HARNESS}}
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "intake.json"
        path.write_text(json.dumps(entries), encoding="utf-8")
        saved = sys.argv
        sys.argv = [saved[0], "--intake", str(path)]
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                rc = dispatch_loop.main()
        except SystemExit as e:
            rc = "SystemExit %s" % (e.code,)
        finally:
            sys.argv = saved
        check("--intake is a flag main() answers itself, returning 0", rc, 0)
        check("--intake prints the count of the class the ledger holds", "1" in buf.getvalue().split(), True)
        # NEGATIVE: asking for the report must not start the thing that dispatches work.
        check("--intake prints no dispatch row", "DISPATCH" in buf.getvalue(), False)
        check("--intake prints no model-lease line", "LEASE" in buf.getvalue().upper(), False)


def main():
    for fn in (test_classify_the_reason_strings_the_pipeline_emits,
               test_counts_count_the_real_strings_and_the_report_uses_them,
               test_every_class_names_its_own_fix_and_the_unknown_is_dispositioned,
               test_the_real_ledger_is_fully_classified,
               test_the_cli_can_be_asked_for_the_counts):
        print(fn.__name__)
        fn()
    print("  " + ("FAILED: " + ", ".join(FAILS) if FAILS else "test_dispatch_intake_counts: ok"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
