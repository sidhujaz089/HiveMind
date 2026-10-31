#!/usr/bin/env python3
"""The deliberation loop must not stop a model that is still making progress.

Every case runs against a FAKE model, so it is fast, offline, and tests the control flow
rather than a model's mood. The bugs it guards are all real ones from 2026-09-03:

  * a long plan step spanning several rounds counted as a stall, so the loop would have
    stopped exactly the deep thinking it exists to allow
  * a `length` reply written to disk as though it were finished
  * a polish pass that was itself truncated replacing the material it reviewed
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
import deliberate as D                                       # noqa: E402


class Fake:
    """Scripted replies, with SEPARATE queues so nothing is ambiguous.

    work:  [(text, done_reason, tokens), ...]   what the model writes each round
    ticks: [[completed step, ...], ...]         what it claims is finished each time asked
    """

    def __init__(self, plan, work, ticks=None, polish=None, polish_why="stop"):
        self.plan = plan
        self.work = list(work)
        self.ticks = list(ticks or [])
        self.polish = polish if polish is not None else ("POLISHED WHOLE ANSWER " * 40)
        self.polish_why = polish_why

    def __call__(self, model, messages, num_predict, timeout=3600, think=None):
        last = messages[-1]["content"]
        if "PLAN WHAT YOU NEED TO THINK ABOUT" in last:
            return json.dumps({"plan": self.plan}), "stop", 100
        if "which of your plan steps are now COMPLETE" in last:
            done = self.ticks.pop(0) if self.ticks else []
            return json.dumps({"completed": done}), "stop", 50
        if "Write the handoff" in last:
            return "HANDOFF: established X; open Y; stopped at Z.", "stop", 200
        if "made into one piece" in last:
            return self.polish, self.polish_why, 300
        return self.work.pop(0) if self.work else ("", "stop", 10)


def run(plan, work, ticks=None, **kw):
    real = D._call
    D._call = Fake(plan, work, ticks, polish=kw.pop("polish", None),
                   polish_why=kw.pop("polish_why", "stop"))
    try:
        return D.deliberate("qwen3.8:27b-mtp-bf16", "task", "success",
                            log=lambda *_: None, **kw)
    finally:
        D._call = real


def main():
    fails = []
    P = ["step one", "step two", "step three"]

    # 1. A LONG STEP IS NOT A STALL. Three rounds of substantive text completing nothing,
    #    then the plan finishes. The old rule stopped this at two rounds.
    long_text = "x" * 900
    r = run(P,
            work=[(long_text, "length", 900), (long_text, "length", 900),
                  (long_text, "length", 900), (long_text, "stop", 900)],
            ticks=[[], [], []])
    kept_going = r.rounds >= 4
    print(f"  [{'ok' if kept_going else 'BAD'}]    a long step over 4 rounds is not cut off "
          f"as a stall (rounds={r.rounds})")
    if not kept_going:
        fails.append(f"the loop stopped a model that was producing text: {r.why_stopped}")

    # 2. A REAL STALL STOPS. Two rounds of nothing: no step, no text.
    r = run(P, work=[("", "length", 5)] * 4, ticks=[[], [], []])
    stopped = not r.done and "progress" in r.why_stopped
    print(f"  [{'ok' if stopped else 'BAD'}]    two empty rounds stop it for lack of progress")
    if not stopped:
        fails.append(f"an actual stall did not stop the loop: {r.why_stopped!r}")

    # 3. AN UNFINISHED RUN SAYS SO. done=False and the outstanding steps are named.
    r = run(P, work=[("partial", "length", 10)] * 4, ticks=[["step one"], [], []])
    honest = (not r.done) and r.outstanding
    print(f"  [{'ok' if honest else 'BAD'}]    an unfinished run reports done=False with "
          f"{len(r.outstanding)} step(s) outstanding")
    if not honest:
        fails.append("an incomplete deliberation did not report itself incomplete")

    # 4. A TRUNCATED POLISH MUST NOT REPLACE THE CHUNKS IT REVIEWED.
    r = run(P,
            work=[("y" * 900, "length", 900), ("y" * 900, "length", 900),
                  ("y" * 900, "length", 900), ("y" * 900, "length", 900)],
            ticks=[["step one"], ["step two"], []],
            polish="cut off half way", polish_why="length",
            max_chunks=2, ctx_fraction=0.001)
    kept = not r.polished and "cut off half way" not in r.answer
    print(f"  [{'ok' if kept else 'BAD'}]    a truncated polish is discarded, chunks kept")
    if not kept:
        fails.append("a polish pass that was itself cut off replaced the answer it reviewed")

    # 5. FILLING THE CONTEXT HANDS OFF INSTEAD OF STOPPING.
    print(f"  [{'ok' if r.chunks >= 2 else 'BAD'}]    a full context hands off to a new one "
          f"(chunks={r.chunks}, handoffs={len(r.handoffs)})")
    if r.chunks < 2:
        fails.append("the loop did not start a second context when the first filled")

    # 6. A FINISHED REPLY WITH WORK LEFT MUST CONTINUE IN THE SAME CONTEXT (2026-09-04).
    #    The real consultation run finished each reply with done="stop", and the loop treated
    #    that as "context full": handoff, fresh context, four times, at 6% of the window. Each
    #    one discarded everything but a self-written summary.
    r = run(P,
            work=[("first part " * 60, "stop", 900), ("second part " * 60, "stop", 900),
                  ("third part " * 60, "stop", 900)],
            ticks=[["step one"], ["step two"], ["step three"]])
    one_context = r.chunks == 1 and len(r.handoffs) == 0
    print(f"  [{'ok' if one_context else 'BAD'}]    replies that STOP with work left continue "
          f"in one context (chunks={r.chunks}, handoffs={len(r.handoffs)})")
    if not one_context:
        fails.append(f"a stopped reply with outstanding steps opened a new context: "
                     f"chunks={r.chunks}, handoffs={len(r.handoffs)} -- the window was barely "
                     f"touched and the prior context was thrown away")

    # 7. AND PROGRESS MUST BE RECORDED ON THE NORMAL PATH, not only after a truncation. The
    #    consultation answered its questions and reported "plan 0/5 complete".
    done_all = r.done and len(r.completed) == len(P)
    print(f"  [{'ok' if done_all else 'BAD'}]    steps are ticked off when a reply STOPS "
          f"({len(r.completed)}/{len(P)}, done={r.done})")
    if not done_all:
        fails.append(f"completed={r.completed} done={r.done} -- work was done and not counted, "
                     f"so a finished run reports itself INCOMPLETE")

    # 8. F2: THE PROGRESS FILE, AND ITS ORACLE (agreed with the Duo 2026-09-04).
    #    This exercises the REAL _call with a stubbed HTTP layer, not the fake model used
    #    above -- the phases are emitted by the API client, so a test that replaces the
    #    client would be checking its own stub. That distinction is the point of F2: the
    #    broken-pass it guards is a file stamped "generating" BEFORE the request goes out,
    #    which would report thinking during a queue wait. That wrong answer was given three
    #    times on 2026-09-03.
    import tempfile as _tf
    import urllib.request as _u

    class _Resp:
        def __init__(self, payload): self._p = json.dumps(payload).encode()
        def read(self): return self._p
        def __enter__(self): return self
        def __exit__(self, *a): return False

    with _tf.TemporaryDirectory() as td:
        pf = Path(td) / "progress.jsonl"
        D.PROGRESS = pf
        pf.write_text("")
        real_open = _u.urlopen
        try:
            _u.urlopen = lambda req, timeout=None: _Resp(
                {"message": {"content": "hello"}, "done_reason": "stop", "eval_count": 42})
            D._call("qwen3.8:27b-mtp-bf16", [{"role": "user", "content": "hi"}], 100)
        finally:
            _u.urlopen = real_open
            D.PROGRESS = None
        lines = [json.loads(l) for l in pf.read_text().splitlines() if l.strip()]
        phases = [l["phase"] for l in lines]
        ok = phases == ["queued", "generating"]
        print(f"  [{'ok' if ok else 'BAD'}]    one call writes queued then generating "
              f"-> {phases}")
        if not ok:
            fails.append(f"the progress phases for one call were {phases}, expected "
                         f"['queued', 'generating']")
        gen = [l for l in lines if l["phase"] == "generating"]
        waited = gen and "waited_seconds" in gen[0]

        # Q4'S ATTACK ON THIS ORACLE, 2026-09-04, and it was right: "the oracle measures
        # 'generation started', not 'generation succeeded'. A client that receives one token
        # and then errors writes `generating` and passes. done_reason is RECORDED and never
        # CHECKED -- a done_reason=timeout after 3 tokens is indistinguishable from a clean
        # 50,000-token completion." That is the 0/18-for-an-empty-string pattern again: the
        # harness reporting its own state as the model's output. So the oracle now requires
        # the event to carry what actually happened, and a done event to carry real tokens.
        rich = gen and all(k in gen[0] for k in ("done_reason", "tokens", "chars"))
        print(f"  [{'ok' if rich else 'BAD'}]    `generating` records done_reason, tokens and "
              f"chars -- so 'started' can be told from 'succeeded'")
        if not rich:
            fails.append("a `generating` event does not carry done_reason/tokens/chars, so a "
                         "one-token failure is indistinguishable from a completed answer")
        print(f"  [{'ok' if waited else 'BAD'}]    `generating` carries waited_seconds, so "
              f"queue time is separable from thinking time")
        if not waited:
            fails.append("a `generating` event has no waited_seconds -- queue time and "
                         "thinking time are indistinguishable, the failure F2 exists to fix")

        # THE BROKEN-PASS, MADE CONCRETE: if the loop stamped the phase instead of the
        # client, `generating` would appear with no request having been answered. Prove the
        # ordering is real by checking nothing is written when no call is made.
        pf.write_text("")
        D.PROGRESS = pf
        D._progress("start")
        D.PROGRESS = None
        only_start = [json.loads(l)["phase"] for l in pf.read_text().splitlines() if l.strip()]
        clean = only_start == ["start"]
        print(f"  [{'ok' if clean else 'BAD'}]    no call means no `generating` -> "
              f"{only_start}")
        if not clean:
            fails.append(f"phases appeared without a call: {only_start}")

    # t561: an example object in the prose before the plan must not stand in for the plan
    got = D._json('I will answer like {"step": 1}. Here: {"plan": ["a", "b"]}', "plan")
    if got != {"plan": ["a", "b"]}:
        fails.append(f"a prose-wrapped plan was lost to the example before it: {got!r}")
    for x in fails:
        print("FAIL:", x)
    print("test_deliberate:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
