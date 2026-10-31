#!/usr/bin/env python3
"""A brief that keeps coming to nothing is held. The MODELS are never held.

Jas, 2026-09-16: "Cancel this bench routine. I realized production will not go because of
upstream quality issues. Hold failed tasks but let the models run what they can."

This replaces a per-MODEL breaker written hours earlier, and the wrong unit is the whole lesson.
Twelve of the thirteen recorded production failures were the brief or the harness, not the
model, so benching the model stopped the one part that worked and left it unable to build the
other chunks it could have done. The breaker benched gemma4:26b for three failures that were
all ONE unfixed defect -- t315, its 16000-token output limit.

The measurement is unchanged; only what it is applied to moved. FUTILITY, NOT BLAME: any
non-yield outcome counts, whichever model ran it and whatever the exit code, because the
question is whether this TASK is going anywhere. failures() still answers the different
question of whose fault it was, and still counts exit 1 alone.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

SHA = "a" * 64


def main():
    import dispatch_loop as DL
    fails = []

    def ck(label, got, want):
        ok = got == want
        print(f"  [{'ok' if ok else 'BAD'}]    {label} -> {got}")
        if not ok:
            fails.append(f"{label}: expected {want}, got {got}")

    N = DL.FUTILE_AFTER
    G, Q = "gemma4:26b", "qwen3.8:27b-mtp-bf16"

    def fin(model, ex, accepted=False, at="T", chunk="p99", sha=SHA):
        return {"at": at, "event": "finished", "kind": "production", "chunk": chunk,
                "brief_sha256": sha, "model": model, "exit": ex, "accepted": accepted,
                "seconds": 10}

    # --- THE MODEL-LEVEL BREAKER IS GONE. Cancelled, and this is what keeps it gone.
    ck("there is no per-model bench left", hasattr(DL, "benched"), False)
    ck("nor a bench_state", hasattr(DL, "bench_state"), False)

    # --- futile(): when a TASK is held -----------------------------------------------
    ck(f"{N - 1} dead attempts is not enough", DL.futile("p99", SHA, [fin(G, 1)] * (N - 1))[0],
       False)
    ck(f"{N} real attempt(s) trips it", DL.futile("p99", SHA, [fin(G, 1)] * N)[0], True)
    # A REFUSAL IS NOT AN ATTEMPT -- reversed 2026-09-16, within the hour, on live evidence.
    # Counting exit 2 here parked p17, p18, p19 and p20 inside one tick, each told "the brief or
    # its oracle is the thing to fix", when the real cause was a test THIS session wrote badly:
    # red in every sandbox, so coder.py refused every chunk it touched. Four false accusations
    # from one bad suite. A refusal is a fact about the harness; it gets refusal_storm().
    ck("a refusal is NOT an attempt and does not hold the task",
       DL.futile("p99", SHA, [fin(G, 2)] * (N + 4))[0], False)
    ck("a delivery fault IS an attempt -- the model engaged and nothing landed",
       DL.futile("p99", SHA, [fin(G, 3)] * N)[0], True)
    ck("refusals mixed in are ignored, the real attempts still count",
       DL.futile("p99", SHA, [fin(G, 2), fin(G, 1), fin(Q, 2)])[0], True)
    ck("ACROSS models, because the task is the unit -- one attempt each on two models",
       DL.futile("p99", SHA, [fin(G, 1), fin(Q, 1)])[0], True)
    ck("a yield is not an attempt",
       DL.futile("p99", SHA, [fin(G, DL.YIELD_EXITS[0])] * (N + 3))[0], False)
    ck("an acceptance clears the run",
       DL.futile("p99", SHA, [fin(G, 1)] * N + [fin(G, 0, accepted=True)])[0], False)
    ck("an acceptance resets the count, so only what follows it is weighed",
       DL.futile("p99", SHA, [fin(G, 1), fin(G, 1), fin(G, 0, accepted=True)])[0], False)
    ck("another chunk's attempts are not this one's",
       DL.futile("p99", SHA, [fin(G, 1, chunk="p98")] * N)[0], False)
    ck("EDITING THE BRIEF RELEASES IT -- a new sha is a new brief",
       DL.futile("p99", "z" * 64, [fin(G, 1)] * N)[0], False)
    ck("a preflight failure is not a production attempt",
       DL.futile("p99", SHA, [{**fin(G, 1), "kind": "preflight"}] * N)[0], False)
    ck("the reason points upstream, at the brief",
       "brief" in DL.futile("p99", SHA, [fin(G, 1)] * N)[1], True)

    # --- parked() is where the hold takes effect -------------------------------------
    ck("a futile brief reads as parked", DL.parked("p99", SHA, [fin(G, 1)] * N)[0], True)
    ck("and a brief with no attempts at all does not",
       DL.parked("p99", SHA, [])[0], False)

    # --- plan(): THE MODELS KEEP WORKING ---------------------------------------------
    tmp = Path(tempfile.mkdtemp())
    briefs = tmp / "workspace" / "briefs"
    briefs.mkdir(parents=True)
    for n in ("a.txt", "b.txt"):
        # A TASK: line, or plan() skips both as NO TASK before parked() is reached (49c841a).
        (briefs / n).write_text(f"TASK: t001\nbrief {n}\n")
    real, DL.REPO = DL.REPO, tmp
    try:
        M = [G, Q]
        C = [{"chunk": "pA", "brief": "a.txt", "files": "x.py", "target": "tA"},
             {"chunk": "pB", "brief": "b.txt", "files": "y.py", "target": "tB"}]
        red = lambda t: True                                            # noqa: E731
        sha = {n: DL.brief_sha(n) for n in ("a.txt", "b.txt")}
        cleared = [{"brief_sha256": sha[n], "stopped": False} for n in ("a.txt", "b.txt")]
        # pA is futile; pB is untouched.
        hist = [fin(G, 1, chunk="pA", sha=sha["a.txt"])] * N

        s, k, _ = DL.plan(M, C, [], red, history=hist, objections=cleared, reviewer=Q)
        started = sorted((x["chunk"], x["model"]) for x in s if x["kind"] == "production")
        ck("the futile task is not started", [c for c, _m in started if c == "pA"], [])
        ck("THE OTHER TASK STILL RUNS -- the models are not stopped",
           [c for c, _m in started], ["pB"])
        ck("and the skip names it as parked, pointing upstream",
           "PARKED" in dict(k).get("pA", "") and "brief" in dict(k).get("pA", ""), True)

        # A HELD TASK DOES NOT STOP THE MODEL THAT FAILED IT from taking other work. This is
        # the distinction the per-model breaker got wrong.
        ck("the model that failed pA is still given pB",
           [m for c, m in started if c == "pB"], [G])

        # THE REFUSAL ALARM, which is where exit 2 goes instead.
        refusals = [fin(G, 2, chunk="pA", sha=sha["a.txt"])] * 3
        storm = DL.refusal_storm(refusals)
        ck("repeated refusals are reported as a harness alarm",
           [(c, n) for c, n, _m, _at in storm], [("pA", 3)])
        ck("an acceptance clears the alarm",
           DL.refusal_storm(refusals + [fin(G, 0, accepted=True, chunk="pA",
                                            sha=sha["a.txt"])]), [])
        ck("and a real attempt failure is not a refusal",
           DL.refusal_storm([fin(G, 1, chunk="pA", sha=sha["a.txt"])]), [])
        ck("a chunk no longer enrolled is not reported refused (p47/p186, 2026-10-02)",
           DL.refusal_storm(refusals, enrolled=set()), [])

        # held_tasks() reports for check_workspace.
        held = DL.held_tasks(C, hist)
        ck("held_tasks names the futile chunk", [c for c, _w in held], ["pA"])
        ck("and nothing when nothing is held", DL.held_tasks(C, []), [])

        # FINISHED WORK IS NOT HELD (2026-10-01): all 22 chunks reported held that morning were
        # done -- 18 with their task closed, 4 with a green target on main.
        ck("a green target is not held when red_of is given",
           DL.held_tasks(C, hist, red_of=lambda t: False), [])
        ck("a red one still is", [c for c, _w in DL.held_tasks(C, hist, red_of=lambda t: True)],
           ["pA"])
        done = tmp / "workspace" / "tasks" / "done"
        done.mkdir(parents=True)
        (done / "t001--x.md").write_text("status: done\n")
        ck("a chunk whose task is closed is not held", DL.held_tasks(C, hist), [])
        h2 = [{"event": "started", "chunk": "pB", "pid": 9, "code_state": "S1"},
              {"event": "finished", "chunk": "pB", "pid": 9, "exit": 2}]
        real_cs = DL.code_state
        DL.code_state = lambda: "S1"
        ck("a refusal is not retried on the same code", DL.refused_on("pB", h2), "S1")
        DL.code_state = real_cs
        ck("  an attempt failure is not a refusal", DL.refused_on("pB", h2[:1] + [dict(h2[1], exit=1)]), None)
        ck("and the scheduler skips it without running its suite",
           [w for c, w in DL.classify(C, [], lambda t: 1 / 0, hist, [])[2] if c == "pA"],
           ["task t001 is closed -- nothing to build"])
    finally:
        DL.REPO = real

    for x in fails:
        print("FAIL:", x)
    print("test_futile_task_held:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
