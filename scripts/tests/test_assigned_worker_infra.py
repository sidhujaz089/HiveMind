#!/usr/bin/env python3
"""E1.1 + E1.4 (RCA E-claude-queue-rca.md): infrastructure must not spend MAX_UNUSABLE,
and a give-up must keep the answers its own note points at.

E1.1. `ask_q16.py` exits 3 when the model server is unreachable -- "MODEL SERVER UNREACHABLE --
nothing was asked. Exit 3: retry, do not advance." -- and rc=2 when the answer is INCOMPLETE --
"do not read this as an answer". `assigned_worker.run` folded every non-zero rc except 4 into the
`unusable` counter (E1 measured: 39 of the 40 escalated tasks had at least one zero-character
answer), so a vLLM outage burns MAX_UNUSABLE=2 in minutes and the task lands on Claude with "the
assignment note or the prompt needs fixing" -- a verdict about a conversation that never happened.
`dispatch_loop.failures()` already excludes exits 2 and 3 from a build's failures for exactly this
reason (its docstring: "Neither is the model's doing"); the assigned lane had no such rule.

E1.4. The escalation note says "Its answers are in workspace/assigned-items/<tid>.json", and 39 of
40 escalated tasks' artefacts on disk are `{"unusable": []}`. The answers that note points a reader
at are not in the file it names. The fix records the SHAPE (rc, chars) of every answer in the note
itself, so a reader can tell "rc=3, 0 chars: the server was down" from "rc=0, 900 chars: the model
answered badly" -- rc is what distinguishes them, and the file alone cannot say.

All paths isolated: a temp taskstore (TASKS/QUEUE/LOCK), aw.STATE under tmp, a fixture role store,
a stubbed ask. The live task store, the dispatch ledger and any real model are never touched.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import assigned_worker as aw  # noqa: E402
import role_store  # noqa: E402
import taskstore  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        got  {got!r}\n        want {want!r}"))


def main():
    d = {"roles": {"manager": {"name": "Manager", "model": "swift", "kind": "model", "lanes": 4,
                                "tools": {"read_file": True, "run_command": True}},
                   "claude": {"name": "Claude", "kind": "person"},
                   "jas": {"name": "Jas", "kind": "person"},
                   "senior": {"name": "Senior", "model": "qwen3.8:27b-mtp-bf16", "kind": "model"}},
         "_escalation": {"vision": 1, "technical": 2, "rework": 3}}
    _real_escalate = role_store.escalate
    role_store.escalate = lambda cls, from_role=None, dd=None: _real_escalate(cls, from_role, d)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        import skills
        skills.LOADS = tmp / "skill-loads.jsonl"          # never the live log
        tasks = tmp / "tasks"
        tasks.mkdir()
        taskstore.TASKS, taskstore.DONE = tasks, tasks / "done"
        taskstore.QUEUE, taskstore.LOCK = tasks / "QUEUE.md", tasks / ".lock"
        aw.STATE = tmp / "state"

        a = taskstore.add("Check the tick log", area="dispatch", body="Why is it empty at 03:00?")
        taskstore.set_meta(a, needs="senior")

        def stub(text, rc=0):
            def ask(prompt, model, role, tools):
                return rc, text
            return ask

        # ---- E1.1: rc=3 (server unreachable) spends nothing ---------------------------------
        report("fixture: assigned to senior", taskstore.find(a)[0]["needs"], "senior")
        rc = aw.run(a, "senior", "m:1", ask=stub("", rc=3), d=d)
        meta1, notes1 = taskstore.find(a)
        report("E1.1 rc=3 returns 3 (retry later) -- not 1 (unusable)", rc, 3)
        report("E1.1 rc=3 leaves the task with its role", (meta1["needs"], meta1["status"]),
               ("senior", "queued"))
        report("E1.1 rc=3 writes no give-up note", "could not answer this assignment" in notes1, False)
        report("E1.1 rc=3 records no answer (nothing was answered)", aw._state(a)["unusable"], [])
        report("E1.1 rc=3 records the outage row, with its rc",
               [(r["rc"], r["chars"]) for r in aw._state(a)["infra"]], [(3, 0)])

        # ---- E1.1: rc=2 (INCOMPLETE) spends nothing ------------------------------------------
        rc = aw.run(a, "senior", "m:1", ask=stub("truncated mid-sentence", rc=2), d=d)
        meta2, notes2 = taskstore.find(a)
        report("E1.1 rc=2 returns 2 (retry later)", rc, 2)
        report("E1.1 rc=2 leaves the task with its role", (meta2["needs"], meta2["status"]),
               ("senior", "queued"))
        report("E1.1 rc=2 writes no give-up note", "could not answer this assignment" in notes2, False)
        report("E1.1 rc=2 records the row, with its rc and length",
               [(r["rc"], r["chars"]) for r in aw._state(a)["infra"]],
               [(3, 0), (2, len("truncated mid-sentence"))])
        report("E1.1 an outage storm records no answer", aw._state(a)["unusable"], [])

        # ---- E1.1: an outage storm of any length never escalates ------------------------------
        for _ in range(3):
            rc = aw.run(a, "senior", "m:1", ask=stub("", rc=3), d=d)
        meta3, notes3 = taskstore.find(a)
        report("E1.1 three more outages: rc=3 every time", rc, 3)
        report("E1.1 no roll spent: still senior, no give-up note",
               (meta3["needs"], "could not answer this assignment" in notes3), ("senior", False))
        report("E1.1 the outage rows are kept as evidence (5 rows)",
               [r["rc"] for r in aw._state(a)["infra"]], [3, 2, 3, 3, 3])

        # ---- E1.1: a real unusable answer still counts (the rule is not disabled) ------------
        rc = aw.run(a, "senior", "m:1", ask=stub("an answer with no OUTCOME line at all"), d=d)
        report("E1.1 a real answer with no OUTCOME line is counted as unusable",
               (rc, [r["rc"] for r in aw._state(a)["unusable"]]), (1, [0]))
        report("E1.1 one counted answer alone does not escalate", taskstore.find(a)[0]["needs"], "senior")

        # ---- E1.4: the give-up keeps the answers its note points at ---------------------------
        rc = aw.run(a, "senior", "m:1", ask=stub("second answer, no OUTCOME line"), d=d)
        meta4, notes4 = taskstore.find(a)
        report("E1.4 the second real answer escalates (to the drop-down's role: technical is position 2 "
               "-- claude -- in this fixture's order manager,claude,jas,senior)",
               (rc, meta4["needs"]), (1, "technical"))   # needs: is a tier (2026-10-29): Claude's class
        st = json.loads((aw.STATE / f"{a}.json").read_text(encoding="utf-8"))
        gu = st.get("given_up")
        report("E1.4 the artefact keeps the answers the note cites, in order",
               [r["text"] for r in (gu[0] if gu else [])],
               ["an answer with no OUTCOME line at all", "second answer, no OUTCOME line"])
        report("E1.4 each kept answer carries its rc",
               [r["rc"] for r in (gu[0] if gu else [])], [0, 0])
        report("E1.4 the give-up's outages stay in the file, not in the answers",
               [r["rc"] for r in st.get("infra", [])], [3, 2, 3, 3, 3])
        # The note is the only thing a person reads on the task. It must say WHICH shape each
        # answer had: "rc=3, 0 chars" is an outage, "rc=0, 900 chars" is a model that answered badly.
        a1, a2 = "an answer with no OUTCOME line at all", "second answer, no OUTCOME line"
        report("E1.4 the note quotes each answer's shape (rc + chars)",
               (f"rc=0, {len(a1)} chars" in notes4, f"rc=0, {len(a2)} chars" in notes4), (True, True))
        report("E1.4 the note separates the outages and says they spent no roll",
               ("rc=3" in notes4 and "rc=2" in notes4 and "spent no roll" in notes4), True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
