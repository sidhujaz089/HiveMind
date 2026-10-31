#!/usr/bin/env python3
"""Assigned items: a task Assigned to a MODEL role is worked by that role, not parked.

    python3 scripts/assigned_worker.py                        # what is waiting, per role
    python3 scripts/assigned_worker.py run <task id> --role manager --model <tag>   # the dispatcher runs this

Jas, 2026-09-29 (the Models / Roles / Flow plan, 3b): "a model role: the item goes into that role's
Assigned items queue, a built-in task on every role's list (position editable like any other). The
role works it with the note and the dossier as input." Until this file the Assign to: dropdown
offered every role and a task given to a MODEL role was held forever: manager_next skips every
assigned task, and nothing else looked at it.

WHAT WORKING IT MEANS. The role reads the task and every note on it -- the newest is why it was
assigned -- does what that note asks, and answers in a note on the task. It ends with one line:
    OUTCOME: done              it handled what it was asked; the task is unassigned and goes back
                               into the queue exactly where it was (for most tasks: to the Manager)
    OUTCOME: assign <role>     it needs another role (Jas, Claude, another model role), and says why
An answer without an OUTCOME line, twice, is Assigned to Claude with both answers, because the
prompt or the assignment note is what needs fixing -- the task_worker rule.

WHO TAKES IT. The dispatcher (dispatch_loop.priority_items) queues one item per assigned task for
THAT role, ordered by where "Assigned items" sits on its list and gated by its Interrupt box. A role
whose list does not have "Assigned items" leaves the item waiting, and the tick says so.
"""
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "projects" / "app-repo")]
TASK = "assigned_items"
STATE = REPO / "workspace" / "assigned-items"
MAX_UNUSABLE = 2
# ASK_Q16'S "NOTHING WAS ASKED" EXITS (E1.1, 2026-10-21 RCA). 3 = MODEL SERVER UNREACHABLE
# ("nothing was asked. Exit 3: retry, do not advance."); 2 = INCOMPLETE ("do not read this as an
# answer"). Neither is the model answering badly: E1 measured 39 of 40 escalated tasks with at
# least one zero-character answer, so a vLLM outage burned MAX_UNUSABLE=2 in minutes and the task
# landed on Claude with "the prompt needs fixing" about a conversation that never happened.
# dispatch_loop.failures() already refuses to charge exits 2/3 to a build's model for exactly
# this reason ("Neither is the model's doing"); the assigned lane had no such rule.
INFRA_EXITS = (2, 3)
OUTCOME = re.compile(r"^\s*OUTCOME:\s*(done|assign\s+([\w-]+))\s*$", re.I | re.M)

JOB = """You are the {name} role ({model}) in ~/ai-workspace. A task has been ASSIGNED TO YOU. Assigning
it to you is how someone asks you for something: the newest note on it says what, and why you.

THE TASK, WITH EVERY NOTE ON IT (newest last):

{task}

YOUR JOB. Do what the newest note asks, using the task and the notes above{tools}. Then write your
answer as the note that will be added to the task, signed as you: what you did or found, in plain
words, with the evidence for each claim (a file and line, a command and what it printed). If you
cannot do it, say exactly why.

END WITH EXACTLY ONE LINE, the last line of your answer:
    OUTCOME: done              -- you handled it; the task goes back into the queue where it was
    OUTCOME: assign <role>     -- another role must take it next; roles: {roles}
"""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def model_roles(d=None):
    import role_store
    d = d or role_store.load()
    return {k: r for k, r in d["roles"].items() if r.get("kind", "model") == "model"}


def pending(d=None):
    """[(task id, role)] -- open tasks a model role should work, in queue order.

    A TIER RESOLVES TO ITS HOLDERS (t9540). Before the migration the test was `who in roles`: the field held
    a role key, so it worked. Once needs: holds a tier (rework, technical, vision) that test is false for
    every task and this returned NOTHING -- the Manager's queue went silent while its work sat on disk. It
    now asks role_store.tier_roles(), which accepts both vocabularies, so a rework item still reaches the
    Manager and a half-migrated store still works.
    """
    import taskstore
    import role_store
    roles = set(model_roles(d))
    out = []
    for meta, _ in taskstore.ordered():
        if meta.get("status") in ("done", "cancelled"):
            continue
        # ONE ROW PER TASK, even when a tier has two holders (Claude's decision 2: "first claim wins"
        # between holders, which cannot work if the queue offers the same item to both of them).
        for who in role_store.tier_roles(meta.get("needs"), d)[:1]:
            if who in roles:
                out.append((meta["id"], who))
    return out


def _state(tid):
    p = STATE / f"{tid}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"unusable": []}


def _save_state(tid, st):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / f"{tid}.json").write_text(json.dumps(st, indent=1) + "\n", encoding="utf-8")


def prompt_for(tid, role, d=None):
    import role_store
    import taskstore
    d = d or role_store.load()
    r = d["roles"][role]
    f = next((p for p in taskstore.task_files(include_done=False) if p.name.startswith(f"{tid}--")), None)
    text = f.read_text(encoding="utf-8", errors="replace") if f else f"(no task file for {tid})"
    tools = [t for t, on in (r.get("tools") or {}).items() if on]
    try:
        import dossier                    # plan 3b: "works it with the note AND THE DOSSIER as input"
        text += "\n\n" + dossier.index_text(tid)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        text += f"\n\n(The dossier could not be read: {type(e).__name__}: {e})"
    return JOB.format(name=r.get("name", role), model=r.get("model", ""), task=text,
                      tools=(" and your tools (" + ", ".join(tools) + ")") if tools and role == "manager" else "",
                      roles=", ".join(role_store.assign_order(d)))


def outcome_of(text, roles):
    """('done', '') | ('assign', role) | None. The LAST OUTCOME line counts; an unknown role is None."""
    ms = OUTCOME.findall(text or "")
    if not ms:
        return None
    word, target = ms[-1]
    if word.lower() == "done":
        return "done", ""
    return ("assign", target) if target in roles else None


def _ask(prompt, model, role, tools):
    """Through ask_q16.py: the lease, the cut-off escalation and the ledger. -> (rc, text)."""
    # PRESSING PAUSE HOLDS THIS (Jas, 2026-10-30: "when I press pause it stops every model from working on
    # tasks"). This function is where a model actually starts, so the pause is asked HERE and not only in whatever
    # loop called in: scheduler_commands.must_hold answers for the production screen, for ALL devices, and for the
    # device this work would use.
    try:
        import scheduler_commands as _sc
        _hold, _why = _sc.must_hold()
        if _hold:
            return (1, f"PAUSED: {_why}")
    except Exception as _e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: could not read the pause flags ({type(_e).__name__}: {_e}) -- continuing")
    with tempfile.TemporaryDirectory(prefix="assigned-") as tmp:
        pin, pout = Path(tmp) / "prompt.txt", Path(tmp) / "answer.md"
        pin.write_text(prompt, encoding="utf-8")
        cmd = [sys.executable, str(REPO / "scripts" / "ask_q16.py"), "--prompt", str(pin), "--out", str(pout),
               "--model", model, "--purpose", f"assigned item ({role})"]
        if not tools:
            cmd.append("--no-tools")
        rc = subprocess.call(cmd, cwd=str(REPO), stdin=subprocess.DEVNULL, timeout=4 * 3600)
        return rc, (pout.read_text(encoding="utf-8") if pout.exists() else "")


def run(tid, role, model, ask=_ask, d=None):
    """Work one assigned task. 0 = handled; 4 = the model was busy (nothing asked); 1 = unusable."""
    import role_store
    import skills
    import taskstore
    d = d or role_store.load()
    meta, _ = taskstore.find(tid)
    # TIER-AWARE, like pending() (Claude, 2026-10-29): needs: holds a tier now, so "still mine" means the
    # tier resolves to this role first -- the same first-holder rule pending() offers it by.
    if not meta or role not in role_store.tier_roles(meta.get("needs"), d)[:1]:
        print(f"  {tid} is no longer assigned to {role} -- nothing to do")
        return 0
    tools = role == "manager"          # ask_q16's tools are the Manager's console tools
    prompt, _sk = skills.with_skills(prompt_for(tid, role, d), TASK, role=role, can_open=tools)
    rc, answer = ask(prompt, model, role, tools)
    if rc == 4:
        print("  the model's lease is held -- nothing was asked; the item stays queued")
        return 4
    if rc in INFRA_EXITS:
        # E1.1: ask_q16 says 3 = nothing was asked, 2 = do not read this as an answer. Nothing
        # was answered, so nothing is counted: no unusable row, no give-up, no escalation --
        # the item stays assigned and a later tick retries when the server is back. The row is
        # recorded under "infra" (with rc and length, not the empty text) so the outage is
        # visible in the artefact instead of being booked as a model's failure.
        st = _state(tid)
        st.setdefault("infra", []).append({"at": _now(), "model": model, "rc": rc, "chars": len(answer)})
        _save_state(tid, st)
        print(f"  {tid}: ask_q16 exit {rc} -- nothing was asked; the item stays queued, no roll spent")
        return rc
    roles = set(role_store.assign_order(d))
    out = outcome_of(answer, roles) if rc == 0 else None
    if rc == 0 and out is None and answer.strip():
        # ONE FORMAT RETRY, NOT A NEW ROLL (plan harness #2, 2026-10-19). 32 assignments went to Claude
        # because two long, useful answers each lacked the OUTCOME line. The work is in the answer; only
        # its last line is missing. Ask for that line alone, with the exact error, no tools.
        fix = (f"Your answer below has no valid OUTCOME line (or names a role that does not exist). Reply "
               f"with ONE line and nothing else: `OUTCOME: done` or `OUTCOME: assign <role>`, where <role> "
               f"is one of: {', '.join(role_store.assign_order(d))}.\n\nYOUR ANSWER:\n{answer}")
        rc2, line = ask(fix, model, role, False)
        if rc2 == 0 and outcome_of(line, roles):
            answer = answer.rstrip() + "\n\n" + OUTCOME.search(line).group(0).strip() + "\n"
            out = outcome_of(answer, roles)
            print("  the answer lacked its OUTCOME line; the one-line retry supplied it")
    if out is None:
        st = _state(tid)
        st["unusable"].append({"at": _now(), "model": model, "rc": rc, "text": answer})
        _save_state(tid, st)
        if len(st["unusable"]) >= MAX_UNUSABLE:
            # E1.4 (2026-10-21 RCA): the note says the answers are in the artefact, so the note
            # must say what shape they had. rc is what tells a reader "rc=3, 0 chars: the server
            # was down" from "rc=0, 900 chars: the model answered badly" -- 39 of 40 escalated
            # tasks' artefacts were the empty {"unusable": []} their note pointed at.
            shapes = "; ".join(f"rc={r['rc']}, {len(r['text'])} chars" for r in st["unusable"])
            note = (f"{role} could not answer this assignment usably {len(st['unusable'])} times "
                    f"(no OUTCOME line, or an unknown role): {shapes}. Its answers are in "
                    f"workspace/assigned-items/{tid}.json under \"given_up\". Assigned to Claude: the assignment note "
                    f"or the prompt needs fixing, not another roll.")
            infra = st.get("infra", [])
            if infra:
                counts = {}
                for r in infra:
                    counts[(r["rc"], r["chars"])] = counts.get((r["rc"], r["chars"]), 0) + 1
                infra_txt = "; ".join(f"rc={r}, {c} chars" + (f" x{n}" if n > 1 else "")
                                      for (r, c), n in sorted(counts.items()))
                note += f" Not counted: {len(infra)} calls that asked the model nothing ({infra_txt}); an outage spent no roll."
            taskstore.add_note(tid, note, who="assigned_worker")
            taskstore.set_meta(tid, needs=__import__("role_store").escalate("technical", role))
            # KEPT, NOT WIPED (2026-10-19): the note above points at these answers, and resetting the
            # file to [] left 32 such notes pointing at nothing. The "infra" rows (E1.1) are kept
            # for the same reason: the note quotes them.
            _save_state(tid, {"unusable": [], "infra": infra,
                              "given_up": st.get("given_up", []) + [st["unusable"]]})
        return 1
    body = OUTCOME.sub("", answer).strip()
    taskstore.add_note(tid, body or "(no text before the OUTCOME line)", who=role)
    _to_builder(tid, answer, model)
    if out[0] == "done":
        taskstore.set_meta(tid, needs="")
    else:
        taskstore.set_meta(tid, needs=out[1])
    _keep = _state(tid)
    _save_state(tid, {"unusable": [],
                      **({"infra": _keep["infra"]} if _keep.get("infra") else {}),
                      **({"given_up": _keep["given_up"]} if _keep.get("given_up") else {})})
    print(f"  {tid}: {role} -> {out[0]}{' ' + out[1] if out[1] else ''}")
    return 0


def _to_builder(tid, answer, model):
    """A PLAN IN AN ASSIGNED ANSWER REACHES A BUILDER (t1568, 2026-10-26). Only scoping_queue.sh handed a
    chunks block to bridge_trigger, so an assigned role's plan became a note and nothing else: t1448 and
    t1250 were re-scoped, re-held and looped. Same step, same flags as the queue's: --lint refuses a broken
    scope back to the scoper, --freeze merges a passing one by the gates, --record scores it."""
    import bridge
    import bridge_trigger
    if bridge.NO_BLOCK in bridge.parse(answer)[1]:
        return None
    fd, path = tempfile.mkstemp(prefix=f"{tid}-", suffix=".md")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(answer)
        rc = bridge_trigger.run(path, tid, model, lint=True, freeze=True, record=True)
    finally:
        Path(path).unlink(missing_ok=True)
    print(f"  {tid}: chunks block -> bridge_trigger rc={rc}")
    return rc


def main(argv):
    if argv[:1] == ["run"] and len(argv) >= 2:
        role = argv[argv.index("--role") + 1] if "--role" in argv else ""
        model = argv[argv.index("--model") + 1] if "--model" in argv else ""
        if not role or not model:
            print("run needs --role and --model")
            return 2
        return run(argv[1], role, model)
    for tid, role in pending():
        print(f"  {tid:8} assigned to {role}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
