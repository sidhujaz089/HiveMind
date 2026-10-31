#!/usr/bin/env python3
"""The Claude review queue must never quietly under-report work waiting to be vetted.

t154. Every assertion that matters here is a REFUSAL to drop something: an unreadable ledger
line, a second run of an already-vetted task, a rejection with no stated reason. A queue that
says "empty" when it is not is worse than no queue, because it is trusted.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import reviewq  # noqa: E402

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
    report("the module's own self-test passes", reviewq.self_test(), 0)

    R = [{"at": "1", "task": "t1", "outcome": "ACCEPTED", "branch": "coder/t1"},
         {"at": "2", "task": "t2", "outcome": "NO-OP"},
         {"at": "3", "task": "t3", "outcome": "ACCEPTED-DRYRUN"}]

    print("\nNothing reviewable is dropped:")
    report("a failed run is not presented as reviewable",
           "t2" in [r["task"] for r in reviewq.delivered(R)], False)
    # The one that matters: keying on task alone would hide a re-run behind an old verdict.
    R2 = R + [{"at": "9", "task": "t1", "outcome": "ACCEPTED"}]
    report("a NEW run of a vetted task reappears in the queue",
           len([r for r in reviewq.pending(R2, [{"task": "t1", "at": "1"}])
                if r["task"] == "t1"]), 1)
    report("  and the old one stays gone",
           [r["at"] for r in reviewq.pending(R2, [{"task": "t1", "at": "1"}])
            if r["task"] == "t1"], ["9"])

    print("\nUnreadable ledger lines are reported, never skipped:")
    tmp = Path(tempfile.mkdtemp())
    old = reviewq.RUNS
    try:
        f = tmp / "runs.jsonl"
        f.write_text(json.dumps({"at": "1", "task": "t1", "outcome": "ACCEPTED"})
                     + "\n{ this is not json\n")
        reviewq.RUNS = f
        report("the good row is still read", len(reviewq.runs()), 1)
        report("and the bad row is surfaced", len(reviewq.unreadable()), 1)
    finally:
        reviewq.RUNS = old
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nA verdict must be actionable:")
    rc = reviewq.main(["verdict", "tX", "sent-back"])
    report("sent-back with no --why is REFUSED", rc, 2)
    rc = reviewq.main(["verdict", "tX", "blocked"])
    report("blocked with no --why is REFUSED", rc, 2)

    print("\nsuperseded must not inflate the keep rate:")
    report("superseded is a real verdict word", reviewq.SUPERSEDED in reviewq.VERDICT_WORDS, True)
    src = __import__("inspect").getsource(reviewq.cmd_pareto)
    report("the keep rate excludes superseded", "!= SUPERSEDED" in src, True)
    report("and superseded is not counted as sent-back",
           "(SENT_BACK, BLOCKED)" in src, True)

    print("\nBatch sizing — trust is inherited from the ledger, not from prose:")
    K = {"verdict": "kept", "batch": 3}
    B = {"verdict": "sent-back", "batch": 3}

    # No `batch` key = retrospective / ad-hoc review, which must not license growth.
    RETRO = {"verdict": "kept"}
    report("with no completed batch it starts small, whatever the history",
           reviewq.batch_size([RETRO] * 20)[0], reviewq.BATCH_START)
    report("  and it says why rather than just picking a number",
           "predates the rule" in reviewq.batch_size([RETRO] * 20)[1], True)
    report("a clean batch earns a doubling",
           reviewq.batch_size([K] * 6)[0], 6)
    # The asymmetry: a poor keep rate only slows growth. It costs GPU time, not correctness.
    report("a poor keep rate shrinks but does NOT reset",
           reviewq.batch_size([B] * 5 + [K])[0], 1)
    report("a middling rate holds steady",
           reviewq.batch_size([K, K, K, B, K, B])[0], 3)

    # The refusal that matters most: one defect that got PAST the gate resets everything,
    # no matter how good the surrounding numbers look.
    escaped = [dict(K, task="tBad", escaped=True)]
    report("ONE escaped defect resets to 1 even among 20 clean keeps",
           reviewq.batch_size([K] * 20 + escaped)[0], 1)
    n, why = reviewq.batch_size([K] * 20 + escaped)
    report("  and the reason names the task", "tBad" in why, True)
    report("  but it is not a latch: clean batches vetted AFTER the escape grow it back",
           reviewq.batch_size([K] * 20 + escaped + [dict(K, batch=1)] * 6)[0], 2)
    # prev is the LAST batch run, not the biggest ever. Using max() made it report halving
    # from a historical peak that no recent batch had used.
    report("it grows from the most recent batch, not the largest ever",
           reviewq.batch_size([dict(K, batch=8)] + [dict(K, batch=2)] * 6)[0], 4)
    report("growth never exceeds the cap",
           reviewq.batch_size([dict(K, batch=reviewq.BATCH_CAP)] * 6)[0], reviewq.BATCH_CAP)

    # A verdict that has NO SAY in the keep rate must have no say in the base the rate
    # multiplies. Taking prev from every row ever written let a SUPERSEDED run -- one that
    # was replaced and deliberately excluded from judgement -- triple the unattended batch.
    base = reviewq.batch_size([dict(K, batch=2)] * 5)[0]
    report("five kept runs at batch=2 grow to 4", base, 4)
    report("a SUPERSEDED row cannot raise the batch",
           reviewq.batch_size([dict(K, batch=2)] * 5
                              + [{"verdict": "superseded", "batch": 8}])[0], base)
    report("a HARNESS failure cannot raise the batch either",
           reviewq.batch_size([dict(K, batch=2)] * 5
                              + [{"verdict": "sent-back", "category": "harness",
                                  "batch": 8}])[0], base)
    print("\nKeep rate excludes verdicts on undelivered runs:")
    tmp = Path(tempfile.mkdtemp())
    old_runs, old_vs = reviewq.RUNS, reviewq.VERDICTS
    try:
        runs_file = tmp / "runs.jsonl"
        runs_file.write_text(
            json.dumps({"at": "1", "task": "t1", "outcome": "ACCEPTED", "branch": "coder/t1"}) + "\n"
            + json.dumps({"at": "2", "task": "t2", "outcome": "NO-OP"}) + "\n"
        )
        vs_file = tmp / "verdicts.jsonl"
        vs_file.write_text(
            json.dumps({"at": "1", "task": "t1", "verdict": "kept", "category": "harness"}) + "\n"
            + json.dumps({"at": "2", "task": "t2", "verdict": "sent-back", "category": "harness"}) + "\n"
        )
        reviewq.RUNS = runs_file
        reviewq.VERDICTS = vs_file
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            reviewq.cmd_pareto(None)
        out = buf.getvalue()
        # BOTH rows stay in the denominator; only a KEPT on a provably undelivered run is
        # dropped. This assertion originally expected "vetted 1, kept 1" -- that is, the
        # sent-back on the undelivered run disappearing too. That shrinks the denominator by
        # discarding a genuine record of the model failing, and on the live ledger it moved
        # the rate UP from 7/10 to 8/10: a fix flattering the number it was written to stop
        # flattering. The patch and the test it shipped with were wrong the same way.
        report("a sent-back on an undelivered run stays in the denominator",
               "vetted 2, kept 1" in out, True)
        report("the cause breakdown still sees both", "2 judged" in out, True)
    finally:
        reviewq.RUNS = old_runs
        reviewq.VERDICTS = old_vs
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
