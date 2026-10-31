#!/usr/bin/env python3
"""Three kinds of production failure, and each is accounted for differently.

coder.py's non-zero exits do not mean the same thing, and treating them alike breaks something
in either direction:

  exit 1  attempt-failed        the model tried and the target suite did not go green.
                                SWITCHES model, and earns a supervision decision.
  exit 2  refused-to-start      a precondition failed before any attempt -- a baseline that is
                                not what was declared, an absent target suite, an inference
                                path that did not answer, a live twin job. Does NOT switch: the
                                same precondition fails for the next model too, so switching
                                only spends a second lane to learn the same thing. No decision
                                either -- the log already says exactly what was wrong.
  exit 3  accepted-not-written  the change was judged good and no branch carries it. A delivery
                                fault, so neither a switch nor a judgement call.

Widening the first two accounts to "anything not accepted" on 2026-09-15 was wrong in both, and
this suite is the record of why. What those failures actually needed was to be SEEN -- so every
one of them is in the catalogue, whether or not anything acts on it. Jas, 2026-09-15: "All
failures need documentation, so when we finally get caught up on the backlog, we can go through
the failures and understand how to better fix these at the beginning of the pipeline."

Pure functions over lists of rows; nothing is read from or written to the repository.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

SHA = "a" * 64
A, B = "gemma4:26b", "qwen3.8:27b-mtp-bf16"


def fin(model, exit_code, accepted=False, at="2026-09-15T01:00:00Z", chunk="p99"):
    return {"at": at, "event": "finished", "kind": "production", "chunk": chunk,
            "brief_sha256": SHA, "model": model, "exit": exit_code, "accepted": accepted,
            "seconds": 10, "brief": "brief-p99.txt"}


def main():
    import dispatch_loop as DL
    import supervise
    fails = []

    def ck(label, got, want):
        ok = got == want
        print(f"  [{'ok' if ok else 'BAD'}]    {label} -> {got}")
        if not ok:
            fails.append(f"{label}: expected {want}, got {got}")

    # --- failures(): who is held to have failed, and therefore what switches ---------
    ck("exit 1 is a model failure and switches", DL.failures("p99", SHA, [fin(A, 1)]), [A])
    ck("exit 2 (refused to start) does NOT switch", DL.failures("p99", SHA, [fin(A, 2)]), [])
    ck("exit 3 (not written) does NOT switch", DL.failures("p99", SHA, [fin(A, 3)]), [])
    for y in DL.YIELD_EXITS:
        ck(f"yield exit {y} does not switch", DL.failures("p99", SHA, [fin(A, y)]), [])

    # Jas, 2026-09-15: production goes to Gemma first, and a real failure moves to Qwen.
    ck("a Gemma attempt failure is recorded so the next dispatch goes elsewhere",
       DL.failures("p99", SHA, [fin(A, 1)]), [A])
    ck("both models failing are recorded in order, which is what parks a brief",
       DL.failures("p99", SHA, [fin(A, 1), fin(B, 1)]), [A, B])
    ck("the same model failing twice is one entry, not a switch",
       DL.failures("p99", SHA, [fin(A, 1), fin(A, 1)]), [A])
    ck("a later success clears the history",
       DL.failures("p99", SHA, [fin(A, 1), fin(B, 1), fin(B, 0, accepted=True)]), [])
    ck("another chunk's failure is not this brief's",
       DL.failures("p99", SHA, [fin(A, 1, chunk="p98")]), [])

    # --- pending(): what earns a supervision decision -------------------------------
    ck("an attempt failure is pending a decision",
       [r["chunk"] for r in supervise.pending([fin(A, 1)], [])], ["p99"])
    ck("a refusal earns no decision", [r["chunk"] for r in supervise.pending([fin(A, 2)], [])], [])
    ck("a delivery fault earns no decision",
       [r["chunk"] for r in supervise.pending([fin(A, 3)], [])], [])
    ck("an accepted job is not pending",
       [r["chunk"] for r in supervise.pending([fin(A, 0, accepted=True)], [])], [])

    # A LATER SUCCESS CLOSES IT. p32 failed at 22:01 and delivered at 22:39; leaving it pending
    # spends a decision on a problem that no longer exists.
    ck("a failure followed by a success is not pending",
       [r["chunk"] for r in supervise.pending(
           [fin(A, 1, at="2026-09-15T22:01:25Z"),
            fin(A, 0, accepted=True, at="2026-09-15T22:39:33Z")], [])], [])
    ck("a success then a NEW failure is pending again",
       [r["chunk"] for r in supervise.pending(
           [fin(A, 0, accepted=True, at="2026-09-15T22:00:00Z"),
            fin(A, 1, at="2026-09-15T23:00:00Z")], [])], ["p99"])

    f = fin(A, 1)
    ck("a decided failure is not offered twice",
       [r["chunk"] for r in supervise.pending([f], [{"failure": supervise.key(f)}])], [])
    pf = {**fin(A, 1), "kind": "preflight"}
    ck("a preflight refusal is not a production failure", DL.failures("p99", SHA, [pf]), [])
    ck("a preflight refusal earns no production decision",
       [r["chunk"] for r in supervise.pending([pf], [])], [])

    # --- failure_catalogue(): EVERY failure is documented, acted on or not -----------
    rows = [fin(A, 1, at="T1"), fin(A, 2, at="T2"), fin(B, 3, at="T3"),
            fin(B, 0, accepted=True, at="T4"), fin(A, DL.YIELD_EXITS[0], at="T5")]
    cat = supervise.failure_catalogue(rows, [])
    ck("the catalogue holds all three failure kinds and nothing else",
       sorted(x["kind"] for x in cat),
       ["accepted-not-written", "attempt-failed", "refused-to-start"])
    ck("an accepted job is not in the catalogue", [x for x in cat if x["at"] == "T4"], [])
    ck("a yield is not in the catalogue", [x for x in cat if x["at"] == "T5"], [])
    ck("it is newest first", [x["at"] for x in cat], ["T3", "T2", "T1"])
    ck("each entry carries what the exit code MEANS, not just the number",
       all(x["meaning"] for x in cat), True)

    # THE CATALOGUE IS WHERE THE UNACTIONED FAILURES SHOW UP. A refusal earns no decision, so
    # if it were missing here too it would be documented nowhere -- which is the 7.7-hour case.
    ck("a refusal is in the catalogue even though nothing acts on it",
       [x["kind"] for x in cat if x["at"] == "T2"], ["refused-to-start"])
    ck("a decision is shown against its failure",
       supervise.failure_catalogue([fin(A, 1, at="T1")],
                                   [{"failure": supervise.key(fin(A, 1, at="T1")),
                                     "decision": "HARNESS-BUG"}])[0]["decision"], "HARNESS-BUG")

    # AN EXIT CODE NOBODY DOCUMENTED IS NOT SILENTLY DROPPED.
    ck("an undocumented exit code is catalogued as unclassified",
       [x["kind"] for x in supervise.failure_catalogue([fin(A, 99)], [])], ["unclassified"])

    # EACH FAILURE SHOWS ITS OWN RUN'S LOG (t651). Two runs of one brief: a refusal, then an
    # accepted retry. Keyed by brief, the refusal pointed at the accepted run's log.
    st = lambda pid, log: {"event": "started", "chunk": "p99", "model": A, "brief_sha256": "s",
                           "pid": pid, "log": log}
    refused = dict(fin(A, 2, at="T1"), pid=11, brief_sha256="s")
    ok = dict(fin(A, 0, accepted=True, at="T2"), pid=22, brief_sha256="s")
    ck("a failure's log is its own run's, not the brief's latest",
       [x["log"] for x in supervise.failure_catalogue(
           [st(11, "refused.log"), refused, st(22, "accepted.log"), ok], [])], ["refused.log"])

    for x in fails:
        print("FAIL:", x)
    print("test_failure_accounting:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
