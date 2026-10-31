#!/usr/bin/env python3
"""After a manager job: note the task with what was done, and route it to whoever moves next.

    python3 scripts/manager_note.py t323 workspace/run-logs/manager-queue/out/t323.md

Jas, 2026-09-24: Q4 had answered t323, t367 and t248, and the Hub showed nothing -- a task
worked by the manager looked untouched. The answer names its step, (a) ALREADY DONE, (b) NEEDS JAS,
(c) SCOPE or (d) BLOCKED (scripts/manager_prompt.py asks for exactly that). This adds a note on
the task and sets `needs`: (b) -> jas, so the question lands in Jas's Review tab; (a), (c), (d)
-> claude, whose review files it. An answer with no recognisable step is noted as such and goes
to Claude -- never silently dropped.
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TS = REPO / "projects" / "app-repo" / "taskstore.py"
import os

# WHO EACH STEP GOES TO IS A POSITION ON THE ORG CHART, NOT A NAME (Jas, 2026-10-19). (b) is a
# question only Jas can answer ("vision"); (a) verify-and-close and (d) blocked are "technical".
# role_store.escalate() reads the position from ROLES.json and only ever sends work UP the chart.
# (b) IS "A DECISION ABOVE ME", NOT "JAS" (Jas, 2026-10-19: "I want them assigned to me if clarity is
# needed on ... what am I envisioning. Many of these should go to you as they are over my head.") On
# 10-19 the Manager answered (b) on 11 technical tasks -- a key rotation, a frozen oracle's own bug, a
# registry field -- and every one landed on Jas. (b) now goes to the technical seat, which decides or
# asks Jas a one-line question about what he wants.
STEPS = {"a": ("ALREADY DONE", "technical"), "b": ("NEEDS A DECISION", "technical"), "c": ("SCOPED", "technical"),
         "d": ("BLOCKED", "technical")}

# THE TOKENS THE PROMPT ITSELF USES (E2.1/D.4.1, 2026-10-22 RCA). manager_prompt.py prescribes
# "(b) NEEDS CLAUDE" (L35, the file-creation rule) and "(b) NEEDS JAS" (L101); models also write
# "NEEDS A DECISION", "NEEDS YOUR DECISION", "NEEDS JAS'S DECISION", "NEEDS JAS TO DECIDE".
# step_of recognised only the four canonical words, so an answer that obeyed the prompt verbatim
# parsed as "no step declared" (E2: 3 of 20 sampled; D.4: 138 send-arounds / 65 tasks). The
# letters stay the contract; the words widen to what the prompt asks for.
STEP_WORDS = (r"(?:ALREADY\s+DONE"
              r"|(?:RE[\s\-\u2011]?)?(?:SCOPE|BLOCKED)"
              r"|NEEDS\s+(?:JAS|CLAUDE)(?:[\s'\u2019]+\S+\s+DECISION|\s+TO\s+DECIDE)?"
              r"|NEEDS\s+(?:A\s+|YOUR\s+)?DECISION)")

# THE SECOND (b) IS A DECISION, NOT ANOTHER LAP (E2.2 / D.4 proposal 3, 2026-10-22). D.4 counted
# 138 "The scoper says this NEEDS JAS" notes across 65 tasks -- 73 send-arounds, t728 eight times:
# a model re-reads the same task, reaches the same correct conclusion, and spends the lease of the
# model everyone else wants. Two (b) answers with the same normalised text on one task close the
# loop: ONE terminal note quoting both answers, held on the technical seat.
B_TERMINAL = ("The scoper has said (b) TWICE with the same question: this is a decision, not "
              "another lap. Ask the decision-maker in ONE line -- the question, the options, a "
              "recommendation -- and do not send it back to scope: the third answer will be the "
              "same as the second.")


def b_key(text):
    """A stable digest of a scoping answer's NORMALISED text: what makes two (b) answers "
    "the same question. (E2.2) Whitespace and case are not part of the question."""
    import hashlib
    return hashlib.sha1(" ".join((text or "").split()).lower().encode("utf-8")).hexdigest()[:12]


def asker():
    """The role that wrote the answer: the manager queue exports HIVEMIND_ROLE for each worker."""
    return os.environ.get("HIVEMIND_ROLE") or None


def to(cls):
    """The role key `cls` escalates to from the asking role. Raises role_store.NoEscalation when the
    chart cannot answer -- the job then fails loudly instead of routing to a stale name."""
    sys.path.insert(0, str(REPO / "scripts"))
    import role_store
    return role_store.escalate(cls, asker())


def name_of(key):
    sys.path.insert(0, str(REPO / "scripts"))
    import role_store
    return role_store.load()["roles"].get(key, {}).get("name", key)


def step_of(text):
    """The first '(a)'..'(d)' the answer declares -- anywhere in it. None if there is none.

    E2.1/D.4.1 (2026-10-22): this searched only the first 25 lines. t1431's note records three
    substantively correct passes that failed structurally because the step line landed past line 25
    (the long-head-first rule below keeps an early declaration from being stolen by a later quote;
    the full scan only adds a match where the head found none). The words the prompt prescribes
    ("NEEDS CLAUDE", "NEEDS A DECISION", ...) are in STEP_WORDS; markdown and punctuation between
    the letter and the word are allowed, as t412 established, and RE-SCOPE is a scope (t383).
    """
    head = "\n".join(text.splitlines()[:25])
    pat = re.compile(r"\(([abcd])\)[\s*_\-\u2011\u2013\u2014:.]*" + STEP_WORDS, re.I)
    m = pat.search(head)
    if not m:
        m = pat.search(text)
    return m.group(1).lower() if m else None


def note_for(tid, answer_path, text):
    s = step_of(text)
    label, cls = STEPS.get(s, ("NO STEP DECLARED", "technical"))
    who = to(cls)
    first = next((l.strip("#* ").strip() for l in text.splitlines() if l.strip()), "")[:200]
    return (f"Manager pass (queue top): **{label}** -- {first}. Answer: {answer_path}. "
            f"Waiting on {name_of(who)}."), who


def decide(text, repo=REPO):
    """(status, needs, why) the answer puts the task in, or None: it stays queued for a retry.

    Jas, 2026-09-26: "If it can't be scoped, it should be held for me with notes. Those are
    the only 2 paths from the backlog. Scoped or Needs Jas."
      (a) ALREADY DONE -> queued, held for Claude (Claude verifies and closes it)
      (b) NEEDS JAS    -> queued, held for Jas
      (c) SCOPED       -> doing (the Status board), with a chunks block bridge.py accepts
      (d) BLOCKED      -> blocked, held for Claude (who hands it on to Jas if it is his)
      an answer that is none of these -- no declared step, a prose-only scope, a block
      bridge.py refuses -> held for Jas, and `why` says what was wrong, for the note
      no answer at all (crash, held lease, empty output) -> None: nothing was decided, retry
    """
    if not text.strip():
        return None
    s = step_of(text)
    if not s:
        # A ONE-LINE PROMISE ("I'll verify the line numbers, then write the answer") decides
        # nothing, so it is not Jas's to read: 10 of them were held for him on 2026-09-29/30.
        sys.path.insert(0, str(REPO / "scripts"))
        import ask_q16
        if ask_q16.is_deferral(text):
            return None
    # THE REASON GOES ON THE TASK (2026-10-20): these three wrote why="" and the note read "Reassigned
    # production -> claude ... no reason given" (t1166, t1173). One rank up from the scoper (role_store).
    if s == "a":
        return "queued", to("technical"), "The scoper says this is ALREADY DONE: verify it on main, then close it."
    if s == "b":
        return "queued", to("technical"), "The scoper says this NEEDS JAS: decide whether it does, and ask him if so."
    if s == "d":
        return "blocked", to("technical"), "The scoper says this is BLOCKED: read why in its answer and unblock it."
    if s == "c":
        sys.path.insert(0, str(REPO / "scripts"))
        import bridge
        chunks, errors = bridge.parse(text)
        if not errors:
            errors = bridge.validate(chunks, repo)
        if not errors:
            return "doing", "", ""
        # A SCOPE THAT CANNOT BE BUILT GOES BACK TO SCOPING, NOT TO JAS (2026-10-08, t790: Jas, "I
        # don't know why this is assigned to me"). A missing or invalid chunks block is the scoper's own
        # mistake; the note tells it what to fix. apply() sends it to Claude after SCOPE_RETRIES tries.
        return "queued", "", (UNBUILDABLE + "; ".join(errors[:5]))
    # NO STEP IS THE MANAGER'S OWN FORMAT SLIP, NOT A QUESTION FOR JAS (2026-10-19). This returned
    # "jas": 40 of the 242 tasks in his Review queue got there this way, none needing his vision. It
    # now goes back to scoping with the error, like an unbuildable scope; apply() sends it to Claude
    # after SCOPE_RETRIES identical slips.
    return "queued", "", NO_STEP


UNBUILDABLE = "The manager scoped this but its chunks block cannot be built: "
NO_STEP = ("The manager answered without declaring a step -- (a) done, (b) needs Jas, (c) scope or "
           "(d) blocked -- so it could not be scoped.")
SCOPE_RETRIES = 2


# "ALREADY DONE" IS CHECKED, NOT ESCALATED, WHEN A TEST CAN SAY SO (Jas, 2026-10-20: "can future cases of the
# flow that escalated them to you check them automatically?"). 44 tasks sat with Claude on a scoper's
# step (a). The proof is the task's OWN tests -- the DONE WHEN line of every brief filed for it, and any in
# the task itself -- never tests the answer merely cites; all must pass on main. A hedged answer ("Not the
# whole task" -- t1078's own) is not closed by a machine: it goes up a rank as before.
HEDGE = re.compile(r"not the whole task|partial(?:ly)?\b|remaining\b|still (?:needs|to\b)|not yet\b|except\b"
                   r"|only (?:the|part)", re.I)
DONE_WHEN = re.compile(r"DONE WHEN\s+python3\s+(scripts/tests/test_[\w.-]+\.py)\s+exits 0")
VERIFY_TIMEOUT_S = 300


def task_tests(tid, body="", briefs=None):
    """The scripts/tests/ suites that define 'done' for tid: every brief's DONE WHEN line, and the task's own."""
    found = set(DONE_WHEN.findall(body or ""))
    for b in Path(briefs or (REPO / "workspace" / "briefs")).glob("*.txt"):
        try:
            t = b.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"manager_note: brief {b.name} unreadable ({type(e).__name__}); its tests not counted", file=sys.stderr)
            continue
        if re.search(rf"^TASK:\s*{re.escape(tid)}\s*$", t, re.M):
            found |= set(DONE_WHEN.findall(t))
    return sorted(found)


def verify_done(tid, text, body="", repo=REPO, run=None):
    """(True, evidence) when the scoper's 'already done' is proven by the task's own tests on main;
    (False, why not) otherwise -- and then the task escalates as before."""
    head = "\n".join(text.splitlines()[:15])
    if HEDGE.search(head):
        return False, f"the answer qualifies its 'already done' ({HEDGE.search(head).group(0)!r})"
    tests = task_tests(tid, body)
    if not tests:
        return False, "no test defines done for this task (no DONE WHEN in its briefs or body)"
    missing = [t for t in tests if not (Path(repo) / t).is_file()]
    if missing:
        return False, f"its test(s) are not on main: {', '.join(missing)}"
    run = run or (lambda t: subprocess.run([sys.executable, t], cwd=str(repo), capture_output=True,
                                           timeout=VERIFY_TIMEOUT_S).returncode)
    results = {}
    for t in tests:
        try:
            results[t] = run(t)
        except subprocess.TimeoutExpired:
            results[t] = "timeout"
    red = [f"{t} ({rc})" for t, rc in results.items() if rc != 0]
    if red:
        return False, f"its test(s) are not green on main: {', '.join(red)}"
    return True, "verified on main: " + ", ".join(f"{t} exit 0" for t in tests)


def apply(tid, text, repo=REPO, answer_path="", verify=verify_done):
    """Write decide()'s result onto the task; returns it (None = left queued and unheld)."""
    d = decide(text, repo)
    if d is None:
        return None
    sys.path.insert(0, str(REPO / "projects" / "app-repo"))
    import taskstore
    s = step_of(text)
    if s == "a":
        _, body = taskstore.find(tid)
        ok, evidence = verify(tid, text, body or "", repo)
        if ok and taskstore.set_status(tid, "done") is not False and \
                (taskstore.find(tid)[0] or {}).get("status") == "done":
            taskstore.set_meta(tid, needs="", why=None, who="manager")
            taskstore.add_note(tid, f"The scoper answered ALREADY DONE and the task's own tests prove it -- "
                                    f"{evidence}. Closed automatically. Answer: {answer_path or 'the manager queue'}.",
                               who="manager")
            return ("done", "", evidence)
        d = (d[0], d[1], d[2] + f" Not closed automatically: {evidence}.")
    status, needs, why = d
    if s == "b":
        # E2.2: the SECOND identical (b) on a task is terminal -- one note quoting the answers,
        # held on the technical seat, and no "send it back to scoping" tail. A third identical
        # answer writes nothing: the loop is closed (D.4: 73 send-arounds across 65 tasks, t728
        # eight times). The record of a lap is the [b-key:...] tag apply() writes into its note;
        # (count + 1) // 2 is the number of recorded laps (set_meta + add_note each carry it).
        _, body = taskstore.find(tid)
        rec = f"[b-key:{b_key(text)}]"
        laps = (body.count(rec) + 1) // 2
        if laps >= 2:
            taskstore.set_status(tid, status)
            return (status, needs, B_TERMINAL)
        if laps == 1:
            taskstore.set_status(tid, status)
            taskstore.set_meta(tid, needs=needs, why=None, who="manager")
            taskstore.add_note(tid, f"{B_TERMINAL} Answer: {answer_path or 'the manager queue'}. {rec}",
                               who="manager")
            return (status, needs, B_TERMINAL)
        d = (status, needs, f"{why} [b-key:{b_key(text)}]" if why else f"{why} [b-key:{b_key(text)}]")
    status, needs, why = d
    retry = next((k for k in (UNBUILDABLE, NO_STEP) if why.startswith(k)), None)
    if retry:
        _, body = taskstore.find(tid)
        tries = (body or "").count(retry)
        if tries >= SCOPE_RETRIES:
            needs = to("technical")             # it keeps making the same mistake: escalate up
            why += (f" -- {tries + 1} scopes in a row could not be built, so this goes to "
                    f"{name_of(needs)}.")
            d = (status, needs, why)
        else:
            why += (" -- sent back to scoping: write the answer again WITH its ```chunks block "
                    "(the bridge builds nothing from prose)." if retry == UNBUILDABLE else
                    " -- sent back to scoping: answer again and END with the step line, one of "
                    "(a) done, (b) needs Jas, (c) scope, (d) blocked.")
    taskstore.set_status(tid, status)
    taskstore.set_meta(tid, needs=needs, why=why[:200] or None, who="manager")
    if why:
        tail = (" Read it, then answer here or release the hold to send it back." if needs
                else "")
        taskstore.add_note(tid, f"{why} Answer: {answer_path or 'the manager queue'}.{tail}",
                           who="manager")
    return d


def main(argv):
    if len(argv) != 2:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    tid, ans = argv
    p = Path(ans)
    text = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
    if not text.strip():
        print(f"{tid}: Manager pass produced NO answer ({ans} empty or missing) -> stays queued")
        return 0
    # A FLOW CHANGE IS NOT A SCOPE (plan step 3): its answer is a ```flow-edit block that
    # flow_lint.py judges, applied only when clean -- flow_change.py routes it, not decide().
    sys.path.insert(0, str(REPO / "scripts"))
    import flow_change
    if flow_change.is_flow(tid):
        r = flow_change.answer(tid, ans)
        print(f"{tid}: Flow change -> " + ("nothing decided, stays queued" if r is None
                                           else f"status {r[0]}, assigned {r[1] or '-'}"))
        return 0
    # A SKILL PROPOSAL IS NOT A SCOPE EITHER (plan 3c-i): a ```skill-draft (its own draft) or a
    # ```skill-review (someone else's) -- skill_change.py routes it through the lint and the reviews.
    # THE FREEZE CIRCUIT IS THE MANAGER'S (Jas, 2026-10-01): its ```freeze-decision resets the switch,
    # keeps it off with a fix, or asks Jas (task + phone) -- freeze_circuit.py applies it.
    import freeze_circuit
    if freeze_circuit.is_circuit(tid):
        r = freeze_circuit.answer(tid, ans)
        print(f"{tid}: Freeze circuit -> " + ("nothing decided, stays queued" if r is None
                                             else f"status {r[0]}, assigned {r[1] or '-'}"))
        return 0
    import skill_change
    if skill_change.is_skill(tid):
        r = skill_change.answer(tid, ans)
        print(f"{tid}: Skill proposal -> " + ("nothing decided, stays queued" if r is None
                                              else f"status {r[0]}, assigned {r[1] or '-'}"))
        return 0
    note, _ = note_for(tid, ans, text)
    # NO NOTE on the task (Jas, 2026-09-25: the Status board shows the progress). The answer
    # sets the task's status and hold instead -- decide() above.
    d = apply(tid, text, answer_path=ans)
    import role_store
    if d is not None and d[1] and d[1] == role_store.rank()[0]:     # the top of the chart has the phone
        # HIS PHONE HEARS ABOUT IT (Jas, 2026-10-01: "make a task assigned to me and send my phone a
        # notification about that if it needs me"). A failed send is said, never swallowed.
        try:
            import notify
            notify.send("needs-you", f"{tid} needs your answer", note[:180], url=f"/plan/{tid}")
        except Exception as e:                                      # noqa: BLE001
            print(f"  NOTE: phone notification for {tid} failed: {type(e).__name__}: {e}")
    where = "stays queued for another pass" if d is None else f"status {d[0]}, needs {d[1] or '-'}"
    print(f"{tid}: {note[:120]} -> {where}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
