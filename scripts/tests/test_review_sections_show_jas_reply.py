#!/usr/bin/env python3
"""t163 -- a pre-merge item Jas has answered shows a reply, like the other review section
shows one, and moves out of 'Ready to merge' into its own section so it reads as
'he looked at it, it is back on Claude's plate'.

Jas, 2026-08-26, verbatim: "something that shows a reply to it just like the other review
section. Also if it could be easy enough could it just move it out of the ready for merge
section and put into like a Claude review section so you can know that I've take a look at
it it's back in your plate?"

RED ON MAIN, per test, for the missing change and for nothing else:
  1. test_task_card_marks_a_task_jas_answered_last -- _task_card (feedback.py:554-566) takes
     `body` from ts.find(tid) at 555 and drops it. The reply marker exists only in
     waiting_chip (1125-1135), which the Ready-to-merge cards do not use. So no card built
     from a task whose newest note is Jas's carries the 'you replied' chip. The negative
     assertion is the other half: a task Claude answered last must NOT be marked.
  2. test_answered_task_leaves_ready_and_lands_in_its_own_section -- review_sections has one
     section for vetted branches (596-604). parts has no 'reviewed' key (642), the answered
     task is in parts['ready'], and Ready to merge counts 2 not 1. 'Leaves' is asserted by
     the count and by the id's absence, so a build that adds a section and keeps the card in
     Ready too fails.
  3. test_string_return_keeps_both_sections -- the parts=False return (640-641) is the join
     of `out`; on main no element of `out` carries the new heading. Both tasks must still be
     present: moving is not deleting.
  4. test_review_tab_renders_the_new_section -- the Review tab reads the bucketed dict at
     3827-3831 and adds rv['ready'] + rv['runs'] only. A section emitted into `out` without
     a key in the tuple at 644 lands in 'other', and a section not named at 3829 is never
     rendered. That is a wiring fact about a page built inside do_GET's handler method, so it
     is pinned at the consumer line: the only place a build can satisfy it is the one place
     that puts the section on the page Jas opens.

NO LIVE STATE. sys.modules['reviewq'] is stubbed before feedback is imported, so no
workspace/review-verdicts.jsonl and no workspace/agent-runs.jsonl is opened (reviewq fixes
those paths at import time). feedback.ts.find is patched to return fixture (meta, body) pairs
whose note text is read from files written under tempfile.mkdtemp() -- no workspace/tasks/
read, no /tmp constant, and the note format is the real one scripts/jas_replies.py:31 parses.
feedback.chunk_status is stubbed so chunks_html (541-551) does not parse the 67 MB
workspace/dispatch-loop.jsonl. No subprocess, no urlopen, no model, no live server, no thread.
Imported from parents[2], which is the repository root; no pytest (scripts/tests run as
`python3 <file>`, and the __main__ block at the end is what runs them).
"""
import pathlib
import re
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
sys.path.insert(0, str(ROOT / "scripts"))

REPLIED = "t163"          # Jas's note is the LAST one: he has looked, it is on our plate
UNREPLIED = "t164"        # Claude answered Jas last: it stays in Ready to merge
BRANCH = "t163-phone-review-card"
CHIPPED = "you replied"   # the marker's text, from feedback.py:1134
HEADING = "back with Claude"

# ---- the review ledger, stubbed. Every name below is one feedback.py calls: verdicts (586),
# branch_exists (593), pending (606), runs (628). Two tasks, one branch: DISTINCT ids.
_stub = types.ModuleType("reviewq")
_stub.verdicts = lambda: [
    {"task": REPLIED, "verdict": "kept", "branch": BRANCH,
     "served": "qwen3.8:27b", "why": "the review card on the phone page"},
    {"task": UNREPLIED, "verdict": "kept", "branch": BRANCH,
     "served": "qwen3.8:27b", "why": "a second card, answered by Claude"},
]
_stub.pending = lambda rows=None, vs=None: []
_stub.runs = lambda: []
_stub.branch_exists = lambda name: name == BRANCH
sys.modules["reviewq"] = _stub

import feedback  # noqa: E402

feedback.chunk_status = lambda: {}

# ---- fixture task bodies, on real files, in the note format the repo writes.
# The author token between the middot and the closing stars is what identifies the writer
# (scripts/jas_replies.py:27-31; taskstore.last_speaker, feedback.py:1133, reads the same).
JAS_NOTE = ("**2026-10-20 \u00b7 jas** \u2014 (Jas - Human) "
            "merge it, the phone build works on my handset\n")
CLAUDE_NOTE = ("**2026-10-21 \u00b7 claude-code** \u2014 (Claude - Code) "
               "Answered: yes, and the build is on main.\n")

_META = {
    REPLIED: {"id": REPLIED, "title": "Review sections show my reply",
              "area": "new-idea", "kind": "unfiled", "status": "blocked", "needs": "jas"},
    UNREPLIED: {"id": UNREPLIED, "title": "A second pre-merge item",
                "area": "new-idea", "kind": "unfiled", "status": "blocked", "needs": "jas"},
}


def _fixtures():
    """{task id: (meta, body)} written to a temp dir and read back, so the note text a
    build reads is a real file's contents, not a literal in this file."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    bodies = {
        # Jas spoke last -- the newest note on this task is his.
        REPLIED: "Body for the first task.\n\n## Notes\n\n" + JAS_NOTE,
        # Jas asked, Claude answered after him -- so this one has NOT been looked at.
        UNREPLIED: ("Body for the second task.\n\n## Notes\n\n"
                    + "**2026-10-20 \u00b7 jas** \u2014 (Jas - Human) is this on the phone yet?\n"
                    + CLAUDE_NOTE),
    }
    out = {}
    for tid, body in bodies.items():
        f = tmp / f"{tid}--fixture.md"
        f.write_text(body, encoding="utf-8")
        out[tid] = (_META[tid], f.read_text(encoding="utf-8"))
    return out


def _with_find(patched):
    """Run with feedback.ts.find patched to the fixtures. _task_card (555) and the /review
    card (3791) both reach the store through ts.find, so one patch covers every reader."""
    return patched


def _install(fix):
    old = feedback.ts.find
    feedback.ts.find = lambda tid: fix.get(tid, (None, None))
    return old


def test_task_card_shows_that_jas_answered_last():
    fix = _fixtures()
    old = _install(fix)
    try:
        card = feedback._task_card(REPLIED)
        assert CHIPPED in card, (
            "_task_card shows no reply marker for a task Jas answered last. Today it emits "
            "only the title, the status chip, `extra` and chunks_html -- it binds `body` at "
            "feedback.py:555 and drops it. Card: " + repr(card))
        card2 = feedback._task_card(UNREPLIED)
        assert CHIPPED not in card2, (
            "a task Claude answered LAST is marked as one Jas answered: " + repr(card2))
        assert REPLIED in card, "the card lost its own task id"
        assert UNREPLIED in card2, "the second card lost its own task id"
    finally:
        feedback.ts.find = old


def test_answered_task_leaves_ready_and_lands_in_its_own_section():
    fix = _fixtures()
    old = _install(fix)
    try:
        parts = feedback.review_sections(parts=True)
        assert isinstance(parts, dict), (
            "parts=True must keep returning the bucketed dict; got " + repr(type(parts)))
        assert "reviewed" in parts, (
            "parts dict has no bucket for the new section. On main the dict is "
            "{'ready','waiting','runs','other'} (feedback.py:642) and an unlisted "
            "<!--marker--> is bucketed 'other' at 644. Keys: " + repr(sorted(parts)))
        assert REPLIED not in parts["ready"], (
            "a task Jas has answered is still in Ready to merge -- he asked for it to leave "
            "that section. Ready: " + repr(parts["ready"]))
        assert UNREPLIED in parts["ready"], (
            "a task Claude answered last must STAY in Ready to merge: " + repr(parts["ready"]))
        assert REPLIED in parts["reviewed"], (
            "the answered task did not land in the new section: " + repr(parts["reviewed"]))
        assert UNREPLIED not in parts["reviewed"], (
            "an unanswered task was moved out of Ready to merge: " + repr(parts["reviewed"]))
        assert "Ready to merge (1)" in parts["ready"], (
            "the Ready to merge count must drop with the moved card, not keep counting it: "
            + repr(parts["ready"]))
        assert "Waiting on Technical review" in parts["waiting"], (
            "the Waiting on Technical review section must survive (renamed from Waiting on Claude, 2026-10-29)")
    finally:
        feedback.ts.find = old


def test_string_return_keeps_both_sections():
    fix = _fixtures()
    old = _install(fix)
    try:
        text = feedback.review_sections(parts=False)
        assert isinstance(text, str), "parts=False must still return one string"
        assert HEADING in text, (
            "the string return has no heading for the new section. The parts=False return "
            "(feedback.py:640-641) joins `out`, so the new section must be emitted into "
            "`out`, not only into a bucket. Tail: " + repr(text[-400:]))
        assert REPLIED in text and UNREPLIED in text, (
            "both tasks belong on the page, in different sections -- a move is not a delete")
    finally:
        feedback.ts.find = old


def test_review_tab_renders_the_new_section():
    # The Review tab consumes the bucketed dict (feedback.py:3827-3831). A section that is not
    # named on the consumer line is bucketed 'other' (644) and dropped, so the whole change
    # would be invisible on the page it exists for -- and every other assertion here would
    # still pass. Pinned at the consumer, in the one form it can be pinned: the line is inside
    # do_GET's handler method, which no unit call can reach.
    src = (ROOT / "projects" / "app-repo" / "feedback.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    try:
        i = next(n for n, l in enumerate(lines) if "rv = review_sections(parts=True)" in l)
    except StopIteration:
        raise AssertionError(
            "the /review consumer no longer calls review_sections(parts=True); the window "
            "this test reads has moved -- re-read it before changing the assertion")
    window = "\n".join(lines[i:i + 8])
    assert re.search(r'rest\s*\+=\s*rv\["ready"\][^\n]*rv\["reviewed"\]', window), (
        "the /review consumer at feedback.py:3829 must render rv['reviewed'] alongside "
        "rv['ready']; as written the new section is built, tested green here, and never "
        "appears on the page. Window read: " + repr(window))


if __name__ == "__main__":
    tests = [
        ("task_card_shows_that_jas_answered_last", test_task_card_shows_that_jas_answered_last),
        ("answered_task_leaves_ready_and_lands_in_its_own_section",
         test_answered_task_leaves_ready_and_lands_in_its_own_section),
        ("string_return_keeps_both_sections", test_string_return_keeps_both_sections),
        ("review_tab_renders_the_new_section", test_review_tab_renders_the_new_section),
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS: {name}")
        except AssertionError as e:
            print(f"FAIL: {name}: {e}")
            failures += 1
        except Exception as e:
            print(f"ERROR: {name}: {type(e).__name__}: {e}")
            failures += 1
    if failures:
        print(f"\n{failures}/{len(tests)} failed")
        sys.exit(1)
    print(f"\nAll {len(tests)} tests passed")
    sys.exit(0)
