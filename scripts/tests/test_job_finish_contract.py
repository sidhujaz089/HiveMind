#!/usr/bin/env python3
"""What a finished job records, and when a finished job re-plans the lane it just freed.

Both halves are the 2026-09-15 failure. A script-review's process exits 0 whether its reply was
usable or not, so 29 unreadable reviews of one file were recorded as "exit 0 ACCEPTED" and the
7.7-hour loop was invisible in --status. And plan() is a snapshot taken once per ten-minute cron
tick, so the model that finished those 70-second reviews idled at an 11% duty cycle while a
cleared chunk was skipped for "waiting -- every model has a lane".

Nothing tracked is written: the ledgers are redirected to a temp directory and every subprocess
is replaced.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

REVIEWED = "scripts/truncation_review.py"          # a real file, so sha lookups work
BRIEF = "brief-dispatch-accounting.txt"            # a real brief; run_job hashes it, never writes


def main():
    import dispatch_loop as dl
    import truncation_review as tr
    fails = []

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        dl.LEDGER, tr.LEDGER = tmp / "dispatch.jsonl", tmp / "reviews.jsonl"
        sha = tr.sha(REPO / REVIEWED)

        calls = []
        dl.subprocess.call = lambda *a, **k: calls.append(a) or 0      # the job always "succeeds"
        ticks = []
        dl.retick = lambda: ticks.append(1)

        def finish(kind, review_row=None, rc=0):
            """Run one job to completion with the review ledger in a given state."""
            dl.LEDGER.write_text("")
            tr.LEDGER.write_text(json.dumps(review_row) + "\n" if review_row else "")
            dl.subprocess.call = lambda *a, **k: rc
            ticks.clear()
            # A real brief: run_job hashes the file to look for a checkpoint before it would
            # dispatch. Nothing writes to it.
            job = {"kind": kind, "chunk": REVIEWED, "path": REVIEWED, "brief_sha256": sha,
                   "model": "gemma4:26b", "key": REVIEWED, "files": "", "target": "",
                   "brief": BRIEF}
            dl.run_job(job)
            rows = [json.loads(x) for x in dl.LEDGER.read_text().splitlines() if x.strip()]
            return [r for r in rows if r.get("event") == "finished"][-1], len(ticks)

        # 1. AN UNREADABLE REVIEW IS NOT ACCEPTED, however cleanly the process exited. This is
        #    the row that lied 29 times.
        bad = {"path": REVIEWED, "sha256": sha,
               "status": "unreadable (invalid-json, done_reason=stop)"}
        row, _ = finish("script-review", bad)
        ok = row["accepted"] is False and row["exit"] == 0 and "unreadable" in row["review_status"]
        print(f"  [{'ok' if ok else 'BAD'}]    exit 0 + unreadable reply = NOT accepted "
              f"(accepted={row['accepted']}, status={row.get('review_status')!r})")
        if not ok:
            fails.append(f"a review that came back unreadable was recorded as accepted: {row}")

        # 2. A GOOD REVIEW IS ACCEPTED, and says so in both ledgers.
        row, _ = finish("script-review", {"path": REVIEWED, "sha256": sha, "status": "ok"})
        ok = row["accepted"] is True and row["review_status"] == "ok"
        print(f"  [{'ok' if ok else 'BAD'}]    exit 0 + ok reply = accepted")
        if not ok:
            fails.append(f"a good review was not recorded as accepted: {row}")

        # 3. A MISSING REVIEW ROW IS NOT SILENTLY A PASS. The job claimed to review something
        #    and left no verdict; that is a disagreement, not a success.
        row, _ = finish("script-review", None)
        ok = row["accepted"] is False and row["review_status"] == "no row written"
        print(f"  [{'ok' if ok else 'BAD'}]    a review that wrote no row is not accepted "
              f"({row.get('review_status')!r})")
        if not ok:
            fails.append(f"a review with no ledger row passed: {row}")

        # 4. OTHER KINDS ARE UNCHANGED -- the exit code is still the verdict for production, and
        #    no review_status is invented for them.
        row, _ = finish("production", None)
        ok = row["accepted"] is True and "review_status" not in row
        print(f"  [{'ok' if ok else 'BAD'}]    production still judges on its exit code alone")
        if not ok:
            fails.append(f"production's verdict changed: {row}")

        # 5. A FREED LANE RE-PLANS. The short kinds are the ones that leave a model idle for the
        #    rest of the window.
        _, n = finish("script-review", {"path": REVIEWED, "sha256": sha, "status": "ok"})
        print(f"  [{'ok' if n == 1 else 'BAD'}]    a successful script-review re-plans ({n})")
        if n != 1:
            fails.append(f"a successful short job did not re-plan, so its lane stays idle "
                         f"until cron: {n} retick(s)")

        # 6. A FAILURE DOES NOT RE-PLAN. p32's baseline refusal returned in 83 s and was never
        #    counted as a model failure, so re-planning on failure is a hot loop.
        _, n = finish("script-review", bad)
        print(f"  [{'ok' if n == 0 else 'BAD'}]    an unreadable review does NOT re-plan ({n})")
        if n != 0:
            fails.append(f"a failed job re-planned, which is the hot loop this avoids: {n}")

        # 7. PRODUCTION DOES NOT RE-PLAN EVEN WHEN IT SUCCEEDS. A delivered chunk is
        #    re-dispatched until somebody merges it -- observed on p32 at 22:39:33 -- so this
        #    would rebuild a half-hour job back to back.
        _, n = finish("production", None)
        print(f"  [{'ok' if n == 0 else 'BAD'}]    a successful production job does NOT re-plan "
              f"({n})")
        if n != 0:
            fails.append(f"production re-planned on success; a delivered-but-unmerged chunk "
                         f"would rebuild continuously: {n} retick(s)")

        # 8. A FAILING EXIT NEVER RE-PLANS, whatever the kind.
        _, n = finish("preflight", None, rc=2)
        print(f"  [{'ok' if n == 0 else 'BAD'}]    a failing exit does NOT re-plan ({n})")
        if n != 0:
            fails.append(f"a job that exited non-zero re-planned: {n}")

        # 9. A YIELD DOES RE-PLAN, INCLUDING PRODUCTION. A yield is the harness standing a job
        #    down so something else can run, so the lane is free now and work is already
        #    waiting. At 00:20:03 the tick preempted p32 for 25 queued preflights and started
        #    none of them; the freed lane sat idle for the rest of the window.
        for kind in ("production", "preflight", "script-review"):
            _, n = finish(kind, None, rc=dl.YIELD_EXITS[0])
            print(f"  [{'ok' if n == 1 else 'BAD'}]    a yielded {kind} re-plans ({n})")
            if n != 1:
                fails.append(f"a yielded {kind} did not re-plan, so the lane it freed idles "
                             f"until the next cron tick: {n}")

        # 10. AND A PRODUCTION SUCCESS STILL DOES NOT -- the delivered-but-unmerged rebuild
        #     loop is the reason, and clause 9 must not have reopened it.
        _, n = finish("production", None, rc=0)
        print(f"  [{'ok' if n == 0 else 'BAD'}]    a successful production job still does NOT "
              f"re-plan ({n})")
        if n != 0:
            fails.append(f"production re-planned on success again: {n}")

    for x in fails:
        print("FAIL:", x)
    print("test_job_finish_contract:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
