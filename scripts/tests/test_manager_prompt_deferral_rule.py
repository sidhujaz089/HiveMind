#!/usr/bin/env python3
"""manager_prompt.py: the manager's prompt carries the conditional-future-action rule (t1370).

Origin: workspace/rca/750d6b3bd2a2.md -- turn 56's "If the job ends without loading it,
I'll have the reason from the log" was a sentence, not a thread, because no prompt rule
required a stated conditional intention to carry a task number or a DECLARED-OPEN entry.

Why this oracle asserts VALUES, not presence: the previous scope's oracle was refused
(workspace/tasks/t1370--add-conditional-future-action-rule-to-manager-pr.md:27) with
oracle-weak. scripts/scope_lint.py:329 raises that class when _shape_only() finds no
assertion whose test is anything other than isinstance(...), 'literal' in x,
len(x) == n, a container-type comparison, or a bare assert_called() -- so an oracle built
entirely from 'assert "phrase" in open(source).read()' pins no behaviour. scripts/tests/
test_manager_prompt.py is written that way and is NOT the pattern to copy.

So each test compares a computed value: the parsed bullet's text, a tuple of booleans, the
set of required words, a count, and the string build_prompt() returns for a real task id.

RED today for the missing rule alone: PREAMBLE has no such bullet, _rule_bullet() returns
"" and every test fails on its first comparison -- no import error, no signature the brief
never changes, no pytest, no temp dir.

Read-only: no writes, no subprocess, no sockets, no model calls, no temp dirs, no live
state. Run: python3 scripts/tests/test_manager_prompt_deferral_rule.py (pytest is not used)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
sys.path.insert(0, str(ROOT / "scripts"))

import manager_prompt

RULE_NAME = "Conditional-future-action rule"
MECHANISM_TASK = "task number"
MECHANISM_OPEN = "DECLARED-OPEN"
THREAD_LINE = "A sentence is not a thread."
RECORDER = "deferral"
CONDITIONS = ("boundary", "close-condition")
# The RCA's three conditional shapes, plus the phrasing a deferral is usually written in.
# "if" and "when" appear in ordinary prose, so this list alone cannot stop a stub: the
# bullet-length assert in the first test is what carries that weight.
TRIGGERS = ("if", "when", "as soon as")
TASK_ID = "t1370"


def _rule_bullet(text):
    """The PREAMBLE bullet that carries the rule: the bullet line naming RULE_NAME plus its
    continuation lines, stopping at the next bullet. PREAMBLE is a list of '- ' bullets, so
    a rule merely mentioned in a docstring or a comment is not this bullet.
    """
    out = []
    started = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("- ") and RULE_NAME in s:
            started = True
        elif started and s.startswith("- "):
            break
        if started:
            out.append(s)
    return " ".join(out)


def test_the_preamble_carries_a_bullet_naming_the_rule():
    bullet = _rule_bullet(manager_prompt.PREAMBLE)
    assert bullet != "", "PREAMBLE has no '- ' bullet naming " + repr(RULE_NAME)
    # A rule, not a stub: the RCA's sentence, both mechanisms and the recorder is long prose.
    assert len(bullet) > 200, "the bullet is a stub of " + str(len(bullet)) + " chars: " + bullet


def test_the_rule_offers_both_tracking_mechanisms():
    bullet = _rule_bullet(manager_prompt.PREAMBLE)
    assert (MECHANISM_TASK in bullet, MECHANISM_OPEN in bullet) == (True, True), (
        "the rule must offer a task number AND a DECLARED-OPEN entry -- the two ways a "
        "stated intention becomes a thread; got " + repr(bullet))


def test_the_rule_names_what_a_declared_open_entry_must_carry():
    bullet = _rule_bullet(manager_prompt.PREAMBLE)
    absent = sorted(c for c in CONDITIONS if c not in bullet)
    assert absent == [], "the DECLARED-OPEN option does not name: " + ", ".join(absent)


def test_the_rule_states_the_thread_line_once_and_names_the_recorder():
    bullet = _rule_bullet(manager_prompt.PREAMBLE)
    assert (bullet.count(THREAD_LINE), RECORDER in bullet.lower()) == (1, True), (
        "the rule must state " + repr(THREAD_LINE) + " exactly once and name the "
        "session-review 'deferral' finding that catches a violation; got " + repr(bullet))


def test_the_rule_covers_the_conditional_triggers_the_rca_recorded():
    bullet = _rule_bullet(manager_prompt.PREAMBLE).lower()
    missing = sorted(t for t in TRIGGERS if t not in bullet)
    assert missing == [], ("the rule does not cover the conditional forms " + repr(TRIGGERS)
                           + "; missing " + repr(missing))


def test_the_built_prompt_for_a_task_carries_the_rule():
    """PREAMBLE is not a document -- build_prompt() is what manager_queue.sh runs and what a
    Manager reads. Asserting on its output is the behaviour; asserting on the source file is
    the presence check that was refused.
    """
    try:
        built = manager_prompt.build_prompt(TASK_ID)
    except Exception as exc:                          # said, not swallowed
        raise AssertionError("build_prompt(" + TASK_ID + ") raised "
                             + type(exc).__name__ + ": " + repr(exc))
    assert built.count(RULE_NAME) == 1, (
        "the prompt built for " + TASK_ID + " names the rule "
        + str(built.count(RULE_NAME)) + " time(s); expected 1")
    got = (RULE_NAME in built, MECHANISM_TASK in built, MECHANISM_OPEN in built,
           THREAD_LINE in built, RECORDER in built.lower())
    assert got == (True, True, True, True, True), (
        "the built prompt is missing part of the rule (name, task number, DECLARED-OPEN, "
        "the thread sentence, the deferral recorder) -- got " + repr(got))


def main():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    assert fns, "no test functions defined -- a suite with no tests proves nothing"
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS " + fn.__name__)
        except AssertionError as exc:
            failed += 1
            print("FAIL " + fn.__name__ + ": " + str(exc))
    print(str(len(fns) - failed) + "/" + str(len(fns)) + " passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
