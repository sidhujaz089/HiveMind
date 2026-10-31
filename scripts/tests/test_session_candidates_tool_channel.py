#!/usr/bin/env python3
"""t1039 fail-first oracle: a promise the assistant discharged with a TOOL RESULT in the SAME reply.

Run: python3 scripts/tests/test_session_candidates_tool_channel.py   (no pytest; exits non-zero on failure)

The scripted review hands session_candidates a turn list of (role, text) pairs -- tool traffic is
dropped at the door, and scripts/session_review.py:65 says so in turns()'s own docstring. So a promise
kept by RUNNING the test suite is structurally invisible: it produces no DONE_WORDS token, and the
discharge sits in the SAME assistant reply, which no forward scan from the next turn ever reads.
workspace/tasks/t1039--record-same-reply-tool-results-in-the-session-s.md records the pair this
reproduces: the quote at 2026-08-31T23:02:02.310Z, the edit that did the work at 23:02:08.910Z
(+6.60s), the verification '136 passed, 0 failed' at 23:02:11.413Z. Six point six seconds between a
promise and its proof, and the review lane wrote a false deferral.

Red on the current tree for the missing change, in BOTH of its halves:
  * session_candidates has no discharged_in_reply and no tool_events, so every test that names one
    fails with AttributeError, and
  * candidates(), later_text() and mentioned_later() unpack two names per turn, so a turn that carries
    its tool results -- ('assistant', text, [events]) -- raises ValueError: too many values to unpack.
No /tmp, no subprocess, no network, no model: sc.SEMANTIC is off, so no embedder is imported.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_candidates as sc

sc.SEMANTIC = False

QUOTE = ("It's also guarding something real -- D-30 says reasoning must not be silently disabled -- "
         "and my settings page now lets it be turned off deliberately.")
PROMISE = "I'll make the assertion check behaviour in test_coder_guards.py and record the tension."
LESSON = "I'll write the dsh-local-ai patch-layer lesson later."
PRUNE = "I'll add the NAS prune to the nightly sweep later."
ASSIST_TEXT = PROMISE + " " + QUOTE + " " + LESSON

EDIT_EVENT = {"kind": "write", "tool": "edit", "exit_code": 0,
              "files_written": ["scripts/tests/test_coder_guards.py"],
              "summary": "edited scripts/tests/test_coder_guards.py"}
GREEN_TEST = {"kind": "test", "tool": "bash", "exit_code": 0, "passed": 136, "failed": 0,
              "output": "136 passed, 0 failed"}
RED_TEST = {"kind": "test", "tool": "bash", "exit_code": 1, "passed": 134, "failed": 1,
            "output": "134 passed, 1 failed"}
UNRELATED_WRITE = {"kind": "write", "tool": "edit", "exit_code": 0,
                   "files_written": ["scripts/chatui.py"], "summary": "edited scripts/chatui.py"}
TASK_EVENT = {"kind": "task", "tool": "taskstore", "exit_code": 0, "task": "t1039",
              "output": "created t1039"}
COMMIT_EVENT = {"kind": "commit", "tool": "bash", "exit_code": 0, "commit": "2b89eb1d",
                "output": "[main 2b89eb1d] t1039 record the channel"}
NO_EXIT_CODE = {"kind": "test", "tool": "bash", "passed": 136, "failed": 0,
                "output": "136 passed, 0 failed"}

SESSION = [("user", "The guard test asserts a literal string. Is that going to break when the wording changes?"),
           ("assistant", ASSIST_TEXT, [EDIT_EVENT, GREEN_TEST])]

FAILS = []


def check(label, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + label + (("  [" + detail + "]") if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


def one_row(turns, quote):
    rows = [r for r in sc.candidates(turns) if r["quote"] == quote]
    check("exactly one row for " + quote[:44], len(rows) == 1, str(len(rows)))
    return rows[0] if rows else None


def dropped_quotes(turns):
    return [r["quote"] for r in sc.dropped(turns)]


def t1_the_real_pair_is_discharged_in_its_own_reply():
    r = one_row(SESSION, QUOTE)
    if r is None:
        return
    check("the quote's row type is NOTICED (the trigger that admitted it, RCA D-4)",
          r["type"] == "NOTICED", r["type"])
    ev = sc.discharged_in_reply(SESSION, r)
    check("the same reply carries a structured completion for it", isinstance(ev, dict), repr(ev)[:70])
    if isinstance(ev, dict):
        check("that completion is the test run", ev.get("kind") == "test", repr(ev.get("kind")))
        check("136 passed", ev.get("passed") == 136, repr(ev.get("passed")))
        check("0 failed", ev.get("failed") == 0, repr(ev.get("failed")))
        check("exit_code 0", ev.get("exit_code") == 0, repr(ev.get("exit_code")))
    pr = one_row(SESSION, PROMISE)
    if pr is not None:
        ev2 = sc.discharged_in_reply(SESSION, pr)
        check("the file-naming promise is discharged by the file write",
              isinstance(ev2, dict) and ev2.get("kind") == "write", repr(ev2)[:70])
        check("that write names scripts/tests/test_coder_guards.py",
              isinstance(ev2, dict) and list(ev2.get("files_written") or []) == ["scripts/tests/test_coder_guards.py"],
              repr(ev2.get("files_written") if isinstance(ev2, dict) else ev2))
        check("the two rows ride on two DIFFERENT events, not one blunt per-turn flag",
              isinstance(ev, dict) and isinstance(ev2, dict) and ev.get("kind") != ev2.get("kind"),
              repr([ev.get("kind") if isinstance(ev, dict) else None,
                    ev2.get("kind") if isinstance(ev2, dict) else None]))


def t2_the_discharged_reply_leaves_the_dropped_list():
    flagged = dropped_quotes(SESSION)
    check("the session's own reply discharges all three of its rows", flagged == [], repr(flagged)[:160])


def t3_a_red_test_run_is_not_a_completion():
    s = [("user", "The guard test asserts a literal string."),
         ("assistant", ASSIST_TEXT, [EDIT_EVENT, RED_TEST])]
    r = one_row(s, QUOTE)
    if r is not None:
        ev = sc.discharged_in_reply(s, r)
        check("'134 passed, 1 failed' discharges nothing", ev is None, repr(ev)[:70])
    check("so the quote is still flagged", QUOTE in dropped_quotes(s), repr(dropped_quotes(s))[:160])


def t4_a_write_to_an_unrelated_file_is_not_a_completion():
    s = [("user", "The guard test asserts a literal string."),
         ("assistant", ASSIST_TEXT, [UNRELATED_WRITE])]
    for q in (PROMISE, QUOTE, LESSON):
        r = one_row(s, q)
        if r is not None:
            check("a write to scripts/chatui.py discharges " + q[:32],
                  sc.discharged_in_reply(s, r) is None, repr(sc.discharged_in_reply(s, r))[:70])
    d = dropped_quotes(s)
    check("all three promises stay flagged", len(d) == 3, repr(d)[:200])


def t5_a_tool_result_in_the_next_reply_is_not_a_completion():
    s = [("user", "The guard test asserts a literal string."),
         ("assistant", ASSIST_TEXT, []),
         ("assistant", "Running the suite on the current tree now.", [EDIT_EVENT, GREEN_TEST])]
    r = one_row(s, QUOTE)
    if r is not None:
        check("the NEXT reply's events do not close this reply's promise",
              sc.discharged_in_reply(s, r) is None, repr(sc.discharged_in_reply(s, r))[:70])
    check("so all three promises stay flagged", len(dropped_quotes(s)) == 3, repr(dropped_quotes(s))[:200])


def t6_commit_and_task_events_are_structured_completions():
    s = [("assistant", "I'll file the settings-reasoning guard as t1039.", [TASK_EVENT])]
    r = one_row(s, "I'll file the settings-reasoning guard as t1039.")
    if r is not None:
        ev = sc.discharged_in_reply(s, r)
        check("a tNNN creation is a completion", isinstance(ev, dict) and ev.get("kind") == "task",
              repr(ev)[:70])
        check("it is recorded against t1039", isinstance(ev, dict) and ev.get("task") == "t1039",
              repr(ev.get("task") if isinstance(ev, dict) else ev))
    check("and the promise leaves the dropped list", dropped_quotes(s) == [], repr(dropped_quotes(s))[:160])
    s2 = [("assistant", "I'll land the fix, committed as 2b89eb1d.", [COMMIT_EVENT])]
    r2 = one_row(s2, "I'll land the fix, committed as 2b89eb1d.")
    if r2 is not None:
        ev2 = sc.discharged_in_reply(s2, r2)
        check("a commit is a completion", isinstance(ev2, dict) and ev2.get("kind") == "commit",
              repr(ev2)[:70])
        check("it is recorded against 2b89eb1d", isinstance(ev2, dict) and ev2.get("commit") == "2b89eb1d",
              repr(ev2.get("commit") if isinstance(ev2, dict) else ev2))
    check("and it leaves the dropped list", dropped_quotes(s2) == [], repr(dropped_quotes(s2))[:160])


def t7_exit_code_is_a_required_field_not_decoration():
    s = [("assistant", LESSON, [NO_EXIT_CODE])]
    r = one_row(s, LESSON)
    if r is not None:
        check("a test event with no exit_code is not a success", sc.discharged_in_reply(s, r) is None,
              repr(sc.discharged_in_reply(s, r))[:70])
    check("so the promise stays flagged", dropped_quotes(s) == [LESSON], repr(dropped_quotes(s))[:160])
    s2 = [("assistant", LESSON, [GREEN_TEST])]
    r2 = one_row(s2, LESSON)
    if r2 is not None:
        ev = sc.discharged_in_reply(s2, r2)
        check("the same event with exit_code 0 IS a completion",
              isinstance(ev, dict) and ev.get("passed") == 136 and ev.get("failed") == 0, repr(ev)[:70])
    check("and the promise is closed", dropped_quotes(s2) == [], repr(dropped_quotes(s2))[:160])


def t8_the_channel_is_per_sentence_not_per_turn():
    text = PROMISE + " " + PRUNE
    s = [("assistant", text, [EDIT_EVENT])]
    r = one_row(s, PROMISE)
    if r is not None:
        check("the promise naming the file is discharged by the write naming it",
              isinstance(sc.discharged_in_reply(s, r), dict), repr(sc.discharged_in_reply(s, r))[:70])
    r2 = one_row(s, PRUNE)
    if r2 is not None:
        check("the promise naming no artefact of that write is NOT", sc.discharged_in_reply(s, r2) is None,
              repr(sc.discharged_in_reply(s, r2))[:70])
    d = dropped_quotes(s)
    check("so exactly one row is left, the NAS prune", d == [PRUNE], repr(d)[:200])


def t9_tool_events_reads_the_third_element():
    check("the assistant's events, in order", sc.tool_events(SESSION, 1) == [EDIT_EVENT, GREEN_TEST],
          repr(sc.tool_events(SESSION, 1))[:120])
    check("a turn with no third element gives none", sc.tool_events(SESSION, 0) == [],
          repr(sc.tool_events(SESSION, 0))[:80])
    check("a 2-tuple session gives none", sc.tool_events([("assistant", LESSON)], 0) == [],
          repr(sc.tool_events([("assistant", LESSON)], 0))[:80])
    check("an out-of-range turn gives none", sc.tool_events(SESSION, 99) == [], repr(sc.tool_events(SESSION, 99))[:80])
    check("a non-list third element gives none", sc.tool_events([("assistant", LESSON, "nope")], 0) == [],
          repr(sc.tool_events([("assistant", LESSON, "nope")], 0))[:80])


def t10_malformed_events_are_ignored_not_fatal():
    junk = ["not a dict", {"kind": "test"}, {"kind": "write", "exit_code": 0, "files_written": []},
            {"kind": "test", "exit_code": "0", "passed": "136", "failed": "1"}]
    s = [("assistant", LESSON, junk)]
    r = one_row(s, LESSON)
    if r is not None:
        check("nothing malformed discharges it, and nothing raises", sc.discharged_in_reply(s, r) is None,
              repr(sc.discharged_in_reply(s, r))[:70])
    check("every entry is handed back, said", len(sc.tool_events(s, 0)) == 4, repr(len(sc.tool_events(s, 0))))
    check("so the promise stays flagged", dropped_quotes(s) == [LESSON], repr(dropped_quotes(s))[:160])


def t11_two_tuples_keep_behaving_exactly_as_today():
    s = [("assistant", LESSON)]
    r = one_row(s, LESSON)
    if r is not None:
        check("a 2-tuple turn has no channel", sc.discharged_in_reply(s, r) is None,
              repr(sc.discharged_in_reply(s, r))[:70])
    check("and a promise with no channel is still flagged, as today", dropped_quotes(s) == [LESSON],
          repr(dropped_quotes(s))[:160])
    s2 = [("assistant", LESSON), ("user", "ok"),
          ("assistant", "Filed as t687, the patch-layer lesson is in LESSONS.md.")]
    check("a promise answered in later TEXT is still not flagged (DONE_WORDS / word overlap kept)",
          dropped_quotes(s2) == [], repr(dropped_quotes(s2))[:160])


TESTS = [t1_the_real_pair_is_discharged_in_its_own_reply,
         t2_the_discharged_reply_leaves_the_dropped_list,
         t3_a_red_test_run_is_not_a_completion,
         t4_a_write_to_an_unrelated_file_is_not_a_completion,
         t5_a_tool_result_in_the_next_reply_is_not_a_completion,
         t6_commit_and_task_events_are_structured_completions,
         t7_exit_code_is_a_required_field_not_decoration,
         t8_the_channel_is_per_sentence_not_per_turn,
         t9_tool_events_reads_the_third_element,
         t10_malformed_events_are_ignored_not_fatal,
         t11_two_tuples_keep_behaving_exactly_as_today]

if __name__ == "__main__":
    for t in TESTS:
        print("--- " + t.__name__)
        t()
    print("")
    if FAILS:
        print(str(len(FAILS)) + " of " + str(len(TESTS)) + " test(s) FAILED: " + "; ".join(FAILS[:12]))
        raise SystemExit(1)
    print("all " + str(len(TESTS)) + " tests passed")
