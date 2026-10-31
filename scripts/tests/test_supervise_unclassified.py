#!/usr/bin/env python3
"""t1542: a failed build that ends on a machine verdict word must be EXPLAINED by a rule.

RED ON MAIN today for the stated reason: scripts/supervise.py's rule_decision() ends with
`return None` for any log tail that carries no Traceback, no HTTPError, no bare exception and
no test-failure naming the target suite. So a run that ended
'  last: BAD-SYNTAX - projects/local-inference/coder.py line 2909: ...' (chunk p755-t1463-coder)
and one that ended '  last: TESTS-FAILED -' with its own target suite not red
(chunk p744-t1432-coder) reach no rule, and the caller escalates them
('no rule explains it; escalated to claude') -- which is what writes the `unclassified`
rows this task is the RCA for (supervise.py:523 records the defect class as
DECISION_CLASS.get(decision, "unclassified"), and a run with no decision has no class).

Run: python3 scripts/tests/test_supervise_unclassified.py
(no pytest installed -- running this file runs every assertion; exits non-zero on a failure)

This oracle touches no live state: it builds every log tail from a string constant, writes no
file, starts no process, and never reads a job log from disk.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]            # scripts/tests/ -> repo root
sys.path.insert(0, str(ROOT / "scripts"))
import supervise                                       # noqa: E402

FAILURES = []


def check(cond, msg):
    print(("ok:   " if cond else "FAIL: ") + msg)
    if not cond:
        FAILURES.append(msg)


# The end of a dispatch job log that a builder writes: a run line per attempt, the NOT-ACCEPTED
# line, then the machine verdict the run ended on. The 'last: <WORD>' shape is the one
# supervise.py's own rule reads (its LAST_TESTS_FAILED regex, line 240 of that file).
SYNTAX_TAIL = "\n".join([
    "  RESUMED from attempt 5 (output budget 171847 tokens, window 262144)",
    "  attempt 6 of 7  asked for 4 file(s), gave 4 new (7440 tok, 449s)",
    "  attempt 7 of 7  python syntax (21425 tok, 955s)",
    "",
    "------------------------------------------------------------------------",
    "  NOT ACCEPTED after 7 attempt(s). Nothing was written.",
    "  last: BAD-SYNTAX - projects/local-inference/coder.py line 2909: '(' was never closed",
    "  recorded this failure on t1463 and moved it to the front of QUEUE.md",
])


def test_a_verdict_word_explains_the_run():
    d = supervise.rule_decision(SYNTAX_TAIL, target="scripts/tests/test_coder_output_budget_clock.py")
    check(d is not None, "a run that ends 'last: BAD-SYNTAX ...' gets a decision, not 'no rule explains it'")
    if d is None:
        return
    check(d.get("decision") == "HARNESS-BUG", f"the decision is HARNESS-BUG, not {d.get('decision')!r}")
    why = d.get("why", "")
    check("BAD-SYNTAX" in why, "the why names the verdict word the log ended on")
    check("projects/local-inference/coder.py" in why, "the why names the file the verdict names")
    check("2909" in why, "the why names the line number the verdict names")
    check("t1463" not in why, "the why is built from the log, not from a task id")
    ev = d.get("bug_evidence", "")
    check(bool(ev), "the HARNESS-BUG carries evidence (bug_evidence is not empty)")
    check("NOT ACCEPTED" in ev or "last:" in ev,
          "the evidence includes the end of the log that carried the verdict")


def test_the_verdict_word_is_read_from_the_log_not_hardcoded():
    tail = SYNTAX_TAIL.replace(
        "last: BAD-SYNTAX - projects/local-inference/coder.py line 2909: '(' was never closed",
        "last: BAD-IMPORT - scripts/other_tool.py line 4117: no module named 'nope'")
    d = supervise.rule_decision(tail, target="scripts/tests/test_other_tool.py")
    check(d is not None and d.get("decision") == "HARNESS-BUG",
          "a verdict word no code has ever seen also gets a decision")
    if d is None:
        return
    why = d.get("why", "")
    check("BAD-IMPORT" in why, "the why carries the NEW verdict word (a hard-coded word list cannot)")
    check("scripts/other_tool.py" in why, "the why carries the new file")
    check("4117" in why, "the why carries the new line number")
    check("BAD-SYNTAX" not in why and "2909" not in why,
          "the why is composed from this run's log, not the first case's text")


def test_a_verdict_word_with_no_file_still_explains_the_run():
    tail = "\n".join([
        "  attempt 3 of 5  tests fail (2: test_coder_first_budget.py, test_coder_slice_room.py, 861s)",
        "  stopping: two identical TESTS-FAILED failures",
        "",
        "  NOT ACCEPTED after 5 attempt(s). Nothing was written.",
        "  last: TESTS-FAILED - ",
        "  recorded this failure on t1432 and moved it to the front of QUEUE.md",
    ])
    d = supervise.rule_decision(tail, target="scripts/tests/test_coder_output_budget_clock.py")
    check(d is not None, "a run ending 'last: TESTS-FAILED' with its own target not red is explained too")
    if d is None:
        return
    check(d.get("decision") == "HARNESS-BUG",
          "it is a capability failure: STILL-RED needs the target's own suite red, and it is not")
    check("TESTS-FAILED" in d.get("why", ""), "the why names the verdict word the log carried")
    check("line" not in d.get("why", "").lower(),
          "the why claims no file or line number the log did not give")


def test_the_rules_that_exist_keep_their_precedence():
    crash = ("Traceback (most recent call last):\n"
             '  File "/home/x/projects/local-inference/coder.py", line 91, in ask\n'
             "ValueError: no chunk named\n"
             "  last: BAD-SYNTAX - projects/local-inference/coder.py line 2909: '(' was never closed\n")
    d = supervise.rule_decision(crash, target="scripts/tests/test_x.py")
    check(d is not None and d.get("decision") == "HARNESS-BUG",
          "a Traceback tail is still decided HARNESS-BUG")
    check(d is not None and "ValueError" in d.get("why", "") and "coder.py:91" in d.get("why", ""),
          "the Traceback rule's why is what comes back, not the verdict rule's (it runs first)")

    red = ("  attempt 2 of 3  tests fail (1: test_coder_output_budget_clock.py, 302s)\n"
           "  last: TESTS-FAILED - \n")
    d2 = supervise.rule_decision(red, target="scripts/tests/test_coder_output_budget_clock.py")
    check(d2 is not None and d2.get("decision") == "STILL-RED",
          "a run whose own target suite is still red stays STILL-RED, not HARNESS-BUG "
          f"(got {d2 and d2.get('decision')!r})")


def test_a_run_the_rules_cannot_explain_is_still_escalated():
    check(supervise.rule_decision("  the model answered, the run was cut off, nothing more\n",
                                 target=None) is None,
          "a tail with no machine verdict still returns None -- escalation is not suppressed")
    check(supervise.rule_decision("", target=None) is None, "an empty log still returns None")


if __name__ == "__main__":
    test_a_verdict_word_explains_the_run()
    test_the_verdict_word_is_read_from_the_log_not_hardcoded()
    test_a_verdict_word_with_no_file_still_explains_the_run()
    test_the_rules_that_exist_keep_their_precedence()
    test_a_run_the_rules_cannot_explain_is_still_escalated()
    print("%d assertion(s) failed" % len(FAILURES))
    sys.exit(1 if FAILURES else 0)
