#!/usr/bin/env python3
"""E2.1 + D.4.1 + E2.2 (RCA E-claude-queue-rca.md / D-production-stall-rca.md).

E2.1 / D.4.1. `manager_prompt.py` PRESCRIBES the literal token "(b) NEEDS CLAUDE" (L35, the
file-creation rule) and "(b) NEEDS JAS" (L101, L141, L174). `manager_note.step_of` only recognised
ALREADY DONE / NEEDS JAS / SCOPE / BLOCKED, and only inside the first 25 lines -- so an answer that
obeyed the prompt verbatim parsed as "no step declared" (E2: 3 of the 20 sampled answers were the
file-creation routing; D.4: 138 "The scoper says this NEEDS JAS" notes across 65 tasks, 73 repeat
send-arounds, t728 eight times). step_of must accept the tokens the prompt itself uses, on any line.

E2.2. The second identical (b) on a task is a TERMINAL hold: one note, needs = the technical seat,
and no invitation to send it back to scoping -- not another full scoping lap of a model reading the
same task and reaching the same correct conclusion (t728: eight).

Isolation: a temp taskstore (TASKS/QUEUE/LOCK), a temp repo dir for the answer files, and a stubbed
M.to -- the live task store, the live ROLES.json chart and any real model are never touched.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import manager_note as M  # noqa: E402
import taskstore  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


B_TERM = "The scoper has said (b) TWICE"   # the phrase the terminal note must open with


def bkey(text):
    """M.b_key when the module has it; on main it does not, and the assertions must still run."""
    return M.b_key(text) if hasattr(M, "b_key") else "0" * 12


def main():
    # ---- E2.1 / D.4.1: step_of accepts the tokens the prompt uses, on any line ---------------
    report("canonical (c) SCOPE still parses (regression guard)", M.step_of("## (c) SCOPE\nx"), "c")
    report("canonical (b) NEEDS JAS in the head still parses", M.step_of("(b) NEEDS JAS -- which policy"), "b")
    report("the prompt's own token: (b) NEEDS CLAUDE",
           M.step_of("prose\n(b) NEEDS CLAUDE: create scripts/x.py for the loader."), "b")
    report("markdown-decorated: **(b)** NEEDS CLAUDE",
           M.step_of("x\n**(b)** NEEDS CLAUDE -- scripts/x.py"), "b")
    report("punctuated: (b): NEEDS YOUR DECISION",
           M.step_of("x\n(b): NEEDS YOUR DECISION"), "b")
    report("the decision synonyms: (b) NEEDS A DECISION",
           M.step_of("x\n(b) NEEDS A DECISION"), "b")
    report("  and (b) NEEDS JAS'S DECISION", M.step_of("x\n(b) NEEDS JAS'S DECISION"), "b")
    report("  and (b) NEEDS JAS TO DECIDE", M.step_of("x\n(b) NEEDS JAS TO DECIDE"), "b")
    # D.4.1: the 25-line window is what makes placement a skill requirement (t1431: three
    # substantively correct passes failed structurally).
    long_head = "\n".join(f"analysis line {i}" for i in range(1, 61))
    report("a step declared on line 61 is a step (not only the first 25 lines)",
           M.step_of(long_head + "\n(b) NEEDS CLAUDE: create scripts/x.py."), "b")
    report("a step declared at the end of a long (a) answer",
           M.step_of(long_head + "\n(a) ALREADY DONE: fixed by 1234abc."), "a")
    report("a step declared at the end of a long (d) answer",
           M.step_of(long_head + "\n(d) BLOCKED by t9."), "d")
    # NEGATIVE CASES -- a rule that reads "the file contains (b)" would route any answer it likes.
    report("no step anywhere is no step", M.step_of("prose only, no letters here at all"), None)
    report("a letter without its word is no step", M.step_of("(b) the second option, discussed below"), None)
    report("a word without its letter is no step", M.step_of("This needs Jas to decide, in prose."), None)
    report("a (b) in the head beats a (c) quoted later (head-first, first match)",
           M.step_of("(b) NEEDS JAS -- ask him.\n\nAs documented: (c) SCOPE is the other option."), "b")
    report("a real (c) in the head is not stolen by a (b) quoted after it",
           M.step_of("Answer: (c) SCOPE. See the menu: (b) NEEDS JAS is not mine to take.\n```chunks\n[]\n```"), "c")
    report("the RE-SCOPE spelling (t383) still parses", M.step_of("Step **(c)** — RE-SCOPE: see chunks"), "c")
    report("STEPS keeps the label/class for the new synonyms' letter",
           M.STEPS["b"], ("NEEDS A DECISION", "technical"))

    # ---- E2.2: the second identical (b) is terminal --------------------------------------------
    # apply() writes a note every scoping pass: D.4 counted 138 "The scoper says this NEEDS JAS"
    # notes across 65 tasks. Two identical (b) answers must produce ONE terminal note, and a third
    # identical pass must add no note at all.
    M.to = lambda cls: "claude"                      # fixture chart: (b) -> claude
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tasks = tmp / "tasks"
        tasks.mkdir()
        taskstore.TASKS, taskstore.DONE = tasks, tasks / "done"
        taskstore.QUEUE, taskstore.LOCK = tasks / "QUEUE.md", tasks / ".lock"
        tid = taskstore.add("Add the intake tick to the Flow tab", area="app-repo",
                            body="The Flow tab has no Intake task.")
        q1 = ("## (b) NEEDS CLAUDE\nThe task needs a new module, scripts/intake_tick.py, to hold the "
              "tick. Scope_lint refuses to scope a chunk on a file that does not exist.\n"
              "Question: should Claude create scripts/intake_tick.py empty?")
        p1 = tmp / "out" / "t1.md"
        p1.parent.mkdir()
        p1.write_text(q1, encoding="utf-8")
        # A second pass writes its answer to a NEW file (the manager queue does: out/<id>-<ts>.md),
        # with IDENTICAL question text -- normalised, that is what "the same question" means (E2.2).
        p1b = tmp / "out" / "t1-second.md"
        p1b.write_text("## (b) NEEDS CLAUDE\nThe task needs a new module, scripts/intake_tick.py, to hold the "
                       "tick. scope_lint refuses to scope a chunk on a file that does not exist.\n"
                       "QUESTION: should Claude create scripts/intake_tick.py empty?", encoding="utf-8")

        d1 = M.apply(tid, q1, repo=tmp, answer_path=str(p1))
        body1 = taskstore.find(tid)[1]
        report("first (b): routed to the technical seat, one answer note",
               (d1[0], d1[1], body1.count(f"Answer: {p1}")),
               ("queued", "claude", 1))
        report("  first (b) is NOT terminal", B_TERM[:40] in body1, False)

        d2 = M.apply(tid, p1b.read_text(encoding="utf-8"), repo=tmp, answer_path=str(p1b))
        body2 = taskstore.find(tid)[1]
        report("second identical (b) (same text, new answer file): stays queued with the technical seat",
               (d2[0], d2[1]), ("queued", "claude"))
        report("  the second pass is terminal (named as such)", B_TERM[:40] in body2, True)
        report("  it is ONE terminal note, not a per-lap note",
               body2.count("The scoper has said (b) TWICE"), 1)
        report("  the terminal note quotes the second answer", str(p1b) in body2, True)
        report("  and it tells Claude not to send it back to scoping",
               "do not send it back to scope" in body2, True)

        d3 = M.apply(tid, q1, repo=tmp, answer_path=str(p1))
        body3 = taskstore.find(tid)[1]
        report("third identical (b): no new note (the loop is closed)",
               (body3.count("The scoper has said (b) TWICE"),
                body3.count("Answer: "), body3.count(str(p1))), (1, 2, 1))
        report("  and it stays held, not back to scoping", (taskstore.find(tid)[0]["needs"],
                                                            taskstore.find(tid)[0]["status"]),
               ("technical", "queued"))   # needs: is a tier (2026-10-29)

        # A DIFFERENT question is a real second decision, not a repeat.
        tid2 = taskstore.add("Second task", area="app-repo", body="body")
        q2 = "(b) NEEDS CLAUDE: create scripts/x.py?"
        q3 = "(b) NEEDS CLAUDE: create scripts/y.py? (a different file, a different question)"
        p2 = tmp / "out" / "t2.md"
        p2.write_text(q2, encoding="utf-8")
        p3 = tmp / "out" / "t3.md"
        p3.write_text(q3, encoding="utf-8")
        M.apply(tid2, q2, repo=tmp, answer_path=str(p2))
        M.apply(tid2, q3, repo=tmp, answer_path=str(p3))
        body4 = taskstore.find(tid2)[1]
        report("two (b) answers asking different questions are not a repeat",
               (B_TERM[:40] in body4,
                body4.count(f"[b-key:{bkey(q2)}]") >= 1, body4.count(f"[b-key:{bkey(q3)}]") >= 1),
               (False, True, True))

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
