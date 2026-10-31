#!/usr/bin/env python3
"""The scheduler's decisions, driven through plan() with no model, GPU or process.

Jas, 2026-09-14, the rules being pinned:
  * "Q16 goes through everything it can schedule before it moves to the production line. Then
    works next to the other 2 until everything is done."
  * "If things fail on one model, can the job switch to another one? ... I want to track
    failures and switches. We need a max switch setting that prevents a job from switching
    forever."
And the ones from the first version that still hold: nothing started twice, one lane per model,
green targets skipped, a missing brief named.

The negative cases carry the weight. A reviewer gate that clears everything and a switch rule
that parks after one failure both pass a positive-only fixture.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import dispatch_loop as DL   # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def main():
    tmp = Path(tempfile.mkdtemp())
    briefs = tmp / "workspace" / "briefs"
    briefs.mkdir(parents=True)
    for n in ("a.txt", "b.txt", "c.txt"):
        (briefs / n).write_text(f"TASK: t001\n\nbrief {n}\n")   # every chunk belongs to a task (2026-09-25)
    real_repo, DL.REPO = DL.REPO, tmp
    G, Q16, Q4 = "gemma", "q16", "q4"
    M = [G, Q16, Q4]
    C = [{"chunk": "pA", "brief": "a.txt", "files": "x.py", "target": "tA"},
         {"chunk": "pB", "brief": "b.txt", "files": "y.py", "target": "tB"},
         {"chunk": "pC", "brief": "c.txt", "files": "z.py", "target": "tC"}]
    red = lambda t: True  # noqa: E731
    sha = {n: DL.brief_sha(n) for n in ("a.txt", "b.txt", "c.txt")}
    clear = lambda *names: [{"brief_sha256": sha[n], "stopped": False} for n in names]  # noqa: E731
    starts = lambda s: [(x["chunk"], x["model"], x["kind"]) for x in s]  # noqa: E731
    why = lambda k, c: dict(k).get(c, "")  # noqa: E731

    try:
        # ---- REVIEW FIRST: unreviewed briefs go to Q16 only, one at a time, before production
        s, k, idle = DL.plan(M, C, [], red, history=[], objections=[], reviewer=Q16)
        report("an unreviewed brief goes to Q16 as a review", starts(s), [("pA", Q16, "preflight")])
        # The role that reviews is named from the store, never written in (one source of truth, 2026-10-13).
        import task_routing as _tr
        report("the others wait for the reviewing role, not for a coder",
               why(k, "pB"), f"waiting for {_tr.role_name('preflight')} {DL.REVIEW_WAIT_TAIL}")
        report("no model codes an unreviewed brief", [x for x in s if x["kind"] == "production"], [])
        report("gemma and q4 sit idle rather than code unreviewed work", idle, [G, Q4])

        # ---- once cleared, production: gemma first (the stronger coder), then the rest
        s, k, idle = DL.plan(M, C, [], red, history=[], objections=clear("a.txt", "b.txt", "c.txt"),
                             reviewer=Q16)
        report("cleared briefs go to production, gemma first",
               starts(s), [("pA", G, "production"), ("pB", Q16, "production"), ("pC", Q4, "production")])

        # ---- Q16 reviews what is left BEFORE it takes production work
        s, k, idle = DL.plan(M, C, [], red, history=[], objections=clear("a.txt"), reviewer=Q16)
        report("with reviews pending, Q16 reviews and gemma codes the cleared one",
               starts(s), [("pB", Q16, "preflight"), ("pA", G, "production")])

        # ---- A LANE IS A FREE SLOT (2026-10-06): one model in every role (Swift, 4 slots) with the
        #      manager queue running on it must still take a build; one build per model still holds.
        SW = "swift"
        # PINNED (2026-10-08): Building's Max lanes is read from the role store now, and Jas set it to
        # 0 (no limit) live. These checks are about the one-build default, so they get a store with
        # Building not set; test_max_lanes covers the editable limit.
        _live_routing = DL.ROUTING
        DL.ROUTING = {"roles": {"m": {"kind": "model", "model": SW, "tasks": [{"task": "building"}]}}}
        mgr = [{"model": SW, "kind": "scoping", "pid": 1}]
        s, k, _ = DL.plan([SW], C, mgr, red, history=[], objections=clear("a.txt", "b.txt"),
                          reviewer=None, slots=lambda m: 4)
        report("a 4-slot model running the manager queue still takes a build",
               starts(s), [("pA", SW, "production")])
        report("  but only one: a second build waits for the first",
               # STEP 4 (2026-10-29): the sentence names the RESOURCE -- jobs holding slots, and the slots they
               # hold -- instead of "a free lane", which was unmeasurable and was printed 169 times in a tick
               # whose real constraint was host memory.
               # 2026-10-29: the shortage here is a MODEL, not a slot -- plan() removes a model from `free` after
               # it takes one build, so a second build has no model to go to even when the model has 4 slots. The
               # sentence says that now; it said "no free sequence slot" and sent readers to the wrong number.
               why(k, "pB").split(":")[0], "no free model for this build")
        report("  and the reason carries the counts it decided on",
               "model(s)" in why(k, "pB") and "job(s)" in why(k, "pB"), True)
        s, k, _ = DL.plan([SW], C, mgr, red, history=[], objections=clear("a.txt"), reviewer=None)
        report("control: with the default 1 slot the same model is busy, as before",
               starts(s), [])
        building = mgr + [{"model": SW, "kind": "production", "pid": 2}]
        s, k, _ = DL.plan([SW], C, building, red, history=[], objections=clear("a.txt", "b.txt"),
                          reviewer=None, slots=lambda m: 4)
        report("a model already building takes no second build when Building is not set", starts(s), [])
        DL.ROUTING = _live_routing

        # ---- an objection holds the brief for Claude, and an EDIT sends it back to review
        obj = [{"brief_sha256": sha["a.txt"], "stopped": True}]
        s, k, _ = DL.plan(M, C[:1], [], red, history=[], objections=obj, reviewer=Q16)
        report("an objected brief is not dispatched", s, [])
        report("it is held for Claude", why(k, "pA").startswith("HELD FOR CLAUDE"), True)
        (briefs / "a.txt").write_text("TASK: t001\n\nbrief a, rewritten after the objection\n")
        s, k, _ = DL.plan(M, C[:1], [], red, history=[], objections=obj, reviewer=Q16)
        report("an edited brief goes back to Q16 for review", starts(s), [("pA", Q16, "preflight")])
        (briefs / "a.txt").write_text("TASK: t001\n\nbrief a.txt\n")

        # ---- a reviewer that keeps dying without a verdict is held, not retried forever
        # A REAL RUN HAS A PROCESS. These rows used to carry no pid, which after the 2026-10-28 RCA is
        # exactly what a REFUSED duplicate launch looks like (pid 0, no exit, no duration) -- so the
        # fixture would have proved the opposite of what it means. pid + seconds make them runs.
        crash = [{"event": "finished", "kind": "preflight", "brief_sha256": sha["a.txt"],
                  "exit": 1, "pid": 4242, "seconds": 30}] * 2
        s, k, _ = DL.plan(M, C[:1], [], red, history=crash, objections=[], reviewer=Q16)
        report("two verdict-less reviews hold the brief", why(k, "pA").startswith("HELD FOR CLAUDE"), True)
        # ...and two REFUSED launches, which ran nothing, must NOT hold it.
        refused = [{"event": "finished", "kind": "preflight", "brief_sha256": sha["a.txt"],
                    "exit": None, "pid": 0, "seconds": None}] * 2
        s, k, _ = DL.plan(M, C[:1], [], red, history=refused, objections=[], reviewer=Q16)
        report("  two REFUSED launches do not hold it -- nothing ran",
               why(k, "pA").startswith("HELD FOR CLAUDE"), False)

        # ---- SWITCHING: a model that failed a brief does not get it again
        okA = clear("a.txt")
        fail = lambda m: {"event": "finished", "kind": "production", "chunk": "pA",  # noqa: E731
                          "brief_sha256": sha["a.txt"], "model": m, "exit": 1, "accepted": False}
        # SUPERSEDED 2026-09-16, and recorded rather than deleted. This used to assert that one
        # failure moved the brief to the next free model, and that one failure did not park it.
        # Jas: "we shouldn't have three attempts at a task before failure. It should just be
        # one: note the reason, put it back at the beginning of production, and let it go
        # through analysis again." An automatic switch IS a second attempt, and coder.py is
        # invoked identically on it -- so it could only repeat the first at another half-hour of
        # GPU. The brief is held after one dead attempt and its failure written onto its task;
        # deciding whether another model should try is now the supervisor's judgement rather
        # than the scheduler's reflex.
        s, k, _ = DL.plan(M, C[:1], [], red, history=[fail(G)], objections=okA, reviewer=Q16)
        report("one failure starts nothing -- it goes to analysis, not to the next model",
               [x for x in starts(s) if x[2] == "production"], [])
        report("ONE failure now holds the brief", DL.parked("pA", sha["a.txt"], [fail(G)])[0], True)
        report("and the hold points at the brief, not at the model",
               "brief" in DL.parked("pA", sha["a.txt"], [fail(G)])[1], True)

        # ---- MAX SWITCHES: with MAX_SWITCHES=1, failing on two models parks
        s, k, _ = DL.plan(M, C[:1], [], red, history=[fail(G), fail(Q16)], objections=okA, reviewer=Q16)
        report(f"failing on {DL.MAX_SWITCHES + 1} models parks the job", s, [])
        report("the park names the models and the limit", "max switches" in why(k, "pA"), True)
        report("the same model failing twice is ONE model, not a switch",
               DL.failures("pA", sha["a.txt"], [fail(G), fail(G)]), [G])

        # ---- a refusal is still nobody's FAULT, and still holds the task
        # Two separate accounts, and this pins the difference. failures() asks whose fault it
        # was and still ignores exit 2 -- a broken baseline breaks for the next model too, so a
        # refusal must never move a brief. futile() asks whether the TASK is going anywhere, and
        # a refusal counts there, so one of them holds it. Before 2026-09-16 a refusal did
        # neither, and p32 refused nine times across seven hours with nothing to show for it.
        # A REFUSAL IS NEITHER A FAULT NOR A HOLD. It is a precondition the harness failed to
        # meet -- an untrustworthy baseline, an absent target -- so it must not move the brief to
        # another model (it will fail there too) and must not accuse the brief either. Holding on
        # it was tried for one hour on 2026-09-16 and parked four innocent chunks, all refused by
        # one badly-written test that was red in every sandbox. It gets refusal_storm() instead.
        refusal = {**fail(G), "exit": 2}
        report("a refusal is not a model failure", DL.failures("pA", sha["a.txt"], [refusal]), [])
        report("a refusal does not hold the task either",
               DL.futile("pA", sha["a.txt"], [refusal, refusal, refusal])[0], False)
        s, k, _ = DL.plan(M, C[:1], [], red, history=[refusal], objections=okA, reviewer=Q16)
        report("so the chunk is still dispatched after a refusal",
               starts(s), [("pA", G, "production")])

        # ---- an acceptance clears the failure record
        acc = {**fail(G), "exit": 0, "accepted": True}
        report("an acceptance resets failures", DL.failures("pA", sha["a.txt"], [fail(G), acc]), [])

        # ---- editing the brief releases a park (new sha, no history)
        (briefs / "a.txt").write_text("TASK: t001\n\nbrief a, rewritten after parking\n")
        report("an edited brief is unparked",
               DL.parked("pA", DL.brief_sha("a.txt"), [fail(G), fail(Q16)])[0], False)
        (briefs / "a.txt").write_text("TASK: t001\n\nbrief a.txt\n")

        # ---- the rules from the first version
        running = [{"model": G, "brief": "a.txt"}]
        s, k, _ = DL.plan(M, C, running, red, history=[], objections=clear("a.txt", "b.txt", "c.txt"),
                          reviewer=Q16)
        report("a running brief is not started again", why(k, "pA"), "already running")
        report("a busy model gets no second lane", G in [x["model"] for x in s], False)
        s, k, _ = DL.plan(M, C, [], lambda t: t != "tB", history=[], objections=clear("a.txt", "b.txt", "c.txt"),
                          reviewer=Q16)
        report("a green target is skipped", why(k, "pB"), "target green -- already on main")
        (briefs / "c.txt").unlink()
        s, k, _ = DL.plan(M, C, [], red, history=[], objections=[], reviewer=Q16)
        report("a missing brief is not dispatched", why(k, "pC"), "brief c.txt does not exist")

        # ---- a running PREFLIGHT counts as Q16 being busy (lane_status cannot see it)
        DL.LEDGER = tmp / "ledger.jsonl"
        import os
        DL.record({"event": "started", "kind": "preflight", "chunk": "pA", "model": Q16,
                   "brief": "a.txt", "brief_sha256": "s", "pid": os.getpid()})
        report("a live preflight job is seen as running",
               [r["model"] for r in DL.ledger_running()], [Q16])

        src = (ROOT / "scripts" / "dispatch_loop.py").read_text()
        report("production jobs pass --brief-file", '"--brief-file", brief' in src, True)
        import lane_status as LS
        report("lane_status reads --brief-file", '"--brief-file"' in (ROOT / "scripts" / "lane_status.py").read_text(), True)

        # ---- A PAUSED TICK LEAVES A ROW (2026-09-22).
        # The flag set on 09-20 outlived its builds for two days and the ledger showed
        # nothing: it stopped at 14:50:01Z while the cron fired ~300 more times. Before the
        # fix tick() returned 0 straight from the pause branch, so this asserts the one
        # thing whose absence hid it. Reverting the fix leaves the ledger empty and FAILS.
        import scheduler_commands as SC
        DL.LEDGER = tmp / "paused-ledger.jsonl"
        real_flag, SC.PAUSE_FLAG = SC.PAUSE_FLAG, tmp / "scheduler-paused.enabled"
        try:
            SC.pause(who="a-test", why="proving the tick is recorded")
            report("a paused tick is refused", DL.tick(), 0)
            paused = [r for r in DL.rows() if r.get("event") == "paused"]
            report("a paused tick leaves a ledger row", len(paused), 1)
            report("the row names who paused it", paused[0].get("who") if paused else None,
                   "a-test")
            report("the row carries the reason", paused[0].get("why") if paused else None,
                   "proving the tick is recorded")
            report("a readable flag reports no error",
                   paused[0].get("flag_unreadable") if paused else "missing", None)
            # The flag's contents are a courtesy, not the pause. A corrupt flag must still
            # record -- otherwise the blind spot returns for exactly the flag nobody wrote --
            # and it must SAY the flag was unreadable rather than report a pause by nobody.
            SC.PAUSE_FLAG.write_text("{not json", encoding="utf-8")
            DL.tick()
            corrupt = [r for r in DL.rows() if r.get("event") == "paused"]
            report("a corrupt flag still records the tick", len(corrupt), 2)
            report("a corrupt flag is named, not swallowed",
                   bool(corrupt[-1].get("flag_unreadable")) if len(corrupt) > 1 else False,
                   True)
        finally:
            SC.PAUSE_FLAG = real_flag

        # ---- A COLD LOAD WAITS RATHER THAN EVICTING THE RESIDENT MODEL (2026-09-22).
        # 74 "model predicted to exceed available memory, evicting" events are on record;
        # inference_gate cannot see them coming because a healthy probe says nothing about
        # room. avail_gb and sizes are injected so this asserts the DECISION, not the box.
        Q16, GEM = "qwen3.8:27b-mtp-bf16", "gemma4:26b"
        snap_q16 = {"resident": [{"name": Q16, "gb": 54.7}]}
        sizes = {Q16: 54.7, GEM: 16.9}
        real_dev0 = DL.DEVICE_OF                  # GEM as a GX10 model, whatever SERVERS.json says today
        DL.DEVICE_OF = {k: v for k, v in real_dev0.items() if k != GEM}
        # 16.9 x 1.5 + 12 = ~37.4 GB needed.
        report("no room: a cold load waits",
               bool(DL.memory_block([GEM], snap_q16, avail_gb=20.0, sizes=sizes)), True)
        blocked = DL.memory_block([GEM], snap_q16, avail_gb=20.0, sizes=sizes)
        report("the wait names the model it would load", blocked["model"], GEM)
        report("the wait names what is resident", blocked["holder"], Q16)
        report("room: a cold load proceeds",
               DL.memory_block([GEM], snap_q16, avail_gb=90.0, sizes=sizes), None)
        # An already-resident model costs nothing to "load" and must NEVER be blocked,
        # however little memory is free -- otherwise a full box stops its own running work.
        report("an already-resident model is never blocked",
               DL.memory_block([Q16], snap_q16, avail_gb=1.0, sizes=sizes), None)
        # A model on ANOTHER device never waits on the GX10's memory: fits() budgets it there
        # (2026-10-15, Frontline's 26b on the ThinkPad held by "NO ROOM" for GX10 RAM).
        real_dev = DL.DEVICE_OF
        try:
            DL.DEVICE_OF = {**real_dev, GEM: "thinkpad"}
            report("an off-box model is not gated on GX10 memory",
                   DL.memory_block([GEM], snap_q16, avail_gb=1.0, sizes=sizes), None)
        finally:
            DL.DEVICE_OF = real_dev
        # A gate that cannot measure must not invent a reason to stop production.
        # STUB THE READER, do not pass avail_gb=None. None is memory_block's sentinel for "go
        # and measure", so the first version of this assertion measured THIS BOX: it passed at
        # 95 GB free and failed at 22 GB free, a verdict about the machine's load rather than
        # the code. Worse, a red suite here is an UNDECLARED failure to coder.py's sandbox
        # baseline, so for hours every production dispatch refused to start with
        # "undeclared failure: test_dispatch_loop.py" (2026-09-22/23, p38 and p41). A test
        # that reads the live machine is not a unit test; it is a flaky probe with a veto.
        real_reader = DL._mem_available_gb
        DL._mem_available_gb = lambda: None
        try:
            report("unreadable memory does not block",
                   DL.memory_block([GEM], snap_q16, sizes=sizes), None)
        finally:
            DL._mem_available_gb = real_reader
        report("unreadable model sizes do not block",
               DL.memory_block([GEM], snap_q16, avail_gb=1.0, sizes={}), None)
        report("an unknown tag does not block",
               DL.memory_block(["nosuch:tag"], snap_q16, avail_gb=1.0, sizes=sizes), None)
        report("the biggest cold model decides",
               DL.memory_block([GEM, "nosuch:tag"], snap_q16, avail_gb=20.0,
                               sizes=sizes)["model"], GEM)
    finally:
        DL.REPO = real_repo

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
