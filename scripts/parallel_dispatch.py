#!/usr/bin/env python3
"""Run the outstanding chunks on BOTH models at once, each delivering to its own branch.

    python3 scripts/parallel_dispatch.py --dry-run     # what it would do, and why
    python3 scripts/parallel_dispatch.py               # do it
    python3 scripts/parallel_dispatch.py --models qwen3.8:27b-mtp-bf16,gemma4:26b

WHY, t303, Jas 2026-09-14: *"Today I want both of those models running in parallel and outputs
go to you for the quality gate and merge."*

NOT the hand-off. He was explicit that Gemma-produces-then-Q16-reviews is the FUTURE state --
*"We have too many harness issues to make that operational so current state is parallel."* So
this runs each model on a DIFFERENT chunk, independently, and both land in the review queue
where Claude Code gates and merges. Nothing routes one model's output into the other.

## WHAT IT WILL NOT PROMISE

**Parallel here buys OVERLAP, NOT 2x.** llama-server runs with `-np 1` -- one slot -- so two
models do not decode simultaneously; ollama interleaves them. Measured 2026-09-02: two models
concurrently versus sequentially is **0.92x AGGREGATE throughput**. The win is that one model's
prompt-eval and sandbox setup happen while the other decodes, and that two chunks are in flight
instead of one. Anyone expecting 2x will read a real result as a failure.

## THE RULES, each one a way this goes wrong

1. **ONE CHUNK PER MODEL, NEVER THE SAME CHUNK TWICE.** Two models on one chunk edit the same
   files and deliver two branches that cannot both merge -- and the repo already has six
   branches that are the same brief redelivered, which is most of why the review queue read 18
   when only two items were undecided.
2. **ONLY CHUNKS WHOSE TARGET SUITE IS RED.** A green target means the work is done and the
   autonomy loop already skips it. Dispatching it spends an hour to prove nothing.
3. **REFUSE IF THE INFERENCE PATH IS DOWN.** t283. Four dispatches on 2026-09-11/12 were sent
   into a server that could not answer, and each produced a plausible task-design diagnosis
   that was wrong.
4. **NEVER DISPATCH MORE CHUNKS THAN EXIST.** If one model has no chunk, it sits this round out
   and the report says so. Inventing work to fill a slot is how a queue grows fake entries.
5. **RESULTS ARE APPENDED AS THEY LAND**, one file, never piped through `tail`. A fifty-minute
   run through `| tail -60` produced zero bytes on 2026-09-14 because tail emits nothing until
   its input closes.
6. **EVERY RUN NAMES ITS MODEL.** `--model <tag>`, not the `local` role: before 2026-08-25 every
   row in agent-runs.jsonl recorded the string "local", so the ledger could not say which model
   produced any of them. A parallel run whose rows are all labelled the same is unreadable,
   which defeats the point of running two.
"""
import argparse
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import roles  # noqa: E402  -- the model behind a role comes from SERVING.json
LOOP = REPO / "scripts" / "autonomy_loop.sh"
CHUNK_ROW = re.compile(r"^p\d+-")
RESULTS = REPO / "workspace" / "run-logs" / "parallel-dispatch.log"

# The two Jas named. Q16 is the bf16 tag (his words, 2026-09-14) and Gemma is the 26b MoE.
# ALL THREE, Jas 2026-09-14: "all 3 should be working in parallel; Q4, Q16, and Gemma."
# Q16 is the bf16 tag (his words), Gemma is the 26b MoE, Q4 is the quantised qwen. Ordered
# deliberately, because assignment is positional: the FIRST model gets the FIRST outstanding
# chunk. bf16 leads so the manager-tier model takes the chunk at the top of the loop table.
# Q4 removed 2026-09-14 (Jas: "get rid of Q4"): three models do not fit in memory together.
DEFAULT_MODELS = [roles.for_job("scoping", "qwen3.8:27b-mtp-bf16"), roles.for_job("building", "gemma4:26b")]


def chunks():
    """The enrolled chunks, read from the loop table -- the only list a free model pulls from."""
    out = []
    if not LOOP.is_file():
        return out
    import withdrawals                  # a held/parked chunk returned to the scoper (t1071) is off the line
    gone = withdrawals.load()
    for line in LOOP.read_text(encoding="utf-8", errors="replace").splitlines():
        if CHUNK_ROW.match(line) and line.count("|") >= 3:
            name, brief, files, target = [x.strip() for x in line.split("|")[:4]]
            if name in gone:
                continue
            out.append({"chunk": name, "brief": brief, "files": files, "target": target})
    return out


def target_is_red(target):
    """(red, why). None means it could not be run, which is NOT the same as red."""
    t = REPO / target
    if not t.is_file():
        return None, f"target {target} does not exist -- coder.py refuses an absent target"
    try:
        r = subprocess.run([sys.executable, str(t)], capture_output=True, text=True,
                           timeout=600, cwd=str(REPO), stdin=subprocess.DEVNULL)
    except Exception as e:                                          # noqa: BLE001
        return None, f"could not run {target}: {type(e).__name__}: {e}"
    return (r.returncode != 0), ("red -- there is work to do" if r.returncode
                                 else "already green -- the loop skips it")


def run_one(job, out_lock):
    """One coder.py dispatch. Appends its whole result as it finishes; never raises."""
    brief = REPO / "workspace" / "briefs" / job["brief"]
    cmd = [sys.executable, str(REPO / "projects" / "local-inference" / "coder.py"),
           "--brief-file", str(brief), "--files", *job["files"].split(),
           "--target", job["target"], "--model", job["model"], "--apply"]
    started = time.time()
    try:
        # No timeout here: settings.timeout_for() bounds the MODEL CALL inside coder.py, derived
        # from the measured decode rate and self-tuning from telemetry. A second, hand-picked
        # ceiling out here would be the guess that file exists to remove -- and it would kill a
        # run that was still making progress.
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO),
                           stdin=subprocess.DEVNULL, timeout=None)
        status = f"exit {r.returncode}"
        body = ((r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else ""))
    except Exception as e:                                          # noqa: BLE001
        status, body = f"could-not-run: {type(e).__name__}: {e}", ""
    secs = time.time() - started
    with out_lock:
        with RESULTS.open("a", encoding="utf-8") as f:
            f.write(f"\n##### {job['chunk']}  model={job['model']}  {status}  "
                    f"{secs:.0f}s\n")
            # WHOLE output. This is the only account of what the model did.
            f.write(body if body.endswith("\n") else body + "\n")
    job["status"], job["seconds"] = status, round(secs, 1)
    return job


def main():
    ap = argparse.ArgumentParser(
        description="Run the outstanding chunks on both models at once.")
    ap.add_argument("--models", default=",".join(DEFAULT_MODELS))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    models = [m.strip() for m in a.models.split(",") if m.strip()]

    # RULE 3: the path must answer before anything is spent on it.
    try:
        import inference_health
        h = inference_health.probe()
        if not h.get("up"):
            print("  REFUSING TO DISPATCH -- the inference path did not answer:")
            for line in (h.get("say") or "").splitlines():
                print(f"    {line}")
            return 2
        print(f"  inference path: {h.get('say')}")
    except ImportError:
        print("  NOTE: inference_health not importable; dispatching unchecked.")

    all_chunks = chunks()
    if not all_chunks:
        print("  NO CHUNKS ENROLLED in scripts/autonomy_loop.sh -- nothing to dispatch. "
              "That is not 'all done'; it means the table is empty.")
        return 1

    print(f"\n  {len(all_chunks)} chunk(s) enrolled. Checking which have work:")
    ready = []
    for c in all_chunks:
        red, why = target_is_red(c["target"])
        print(f"    {c['chunk']:18} {why}")
        if red:
            ready.append(c)

    if not ready:
        print("\n  Every enrolled chunk's target suite is GREEN. Nothing to dispatch -- and "
              "that is the honest answer, not a failure.")
        return 0

    # RULES 1 and 4: one chunk per model, no chunk twice, never more jobs than chunks.
    jobs = []
    for model, chunk in zip(models, ready):
        jobs.append({**chunk, "model": model})
    idle = models[len(ready):]
    spare = ready[len(models):]

    print(f"\n  DISPATCHING {len(jobs)} job(s), one chunk per model:")
    for j in jobs:
        print(f"    {j['model']:26} -> {j['chunk']}  (target {j['target']})")
    for m in idle:
        print(f"    {m:26} -> idle this round; there are only {len(ready)} chunk(s) with work")
    for c in spare:
        print(f"    (waiting)                  {c['chunk']} -- more chunks than models")
    print("\n  Parallel here buys OVERLAP, not 2x: llama-server is -np 1, and two models "
          "concurrently measured 0.92x AGGREGATE on 2026-09-02.")

    if a.dry_run:
        print("\n  --dry-run: nothing dispatched.")
        return 0

    if not RESULTS.parent.is_dir():
        # A state directory is created by boot or a migration, never by a writer (mf4):
        # creating it here resurrects a renamed layout mid-migration. Report and stop.
        raise SystemExit(f"parallel_dispatch: {RESULTS.parent} is missing -- a state "
                         f"directory is created by boot or a migration, never by a writer (mf4)")
    with RESULTS.open("a", encoding="utf-8") as f:
        f.write(f"\n{'=' * 70}\n# RUN {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                f"-- {len(jobs)} job(s) in parallel\n")

    lock = threading.Lock()
    threads = []
    for j in jobs:
        t = threading.Thread(target=run_one, args=(j, lock), daemon=False)
        t.start()
        threads.append(t)
        print(f"  started {j['chunk']} on {j['model']}")
    for t in threads:
        t.join()

    print(f"\n  DONE. Full output in {RESULTS.relative_to(REPO)}")
    for j in jobs:
        print(f"    {j['chunk']:18} {j['model']:26} {j.get('status')}  {j.get('seconds')}s")
    print("\n  Delivered branches land in the review queue: python3 scripts/reviewq.py")
    print("  Claude Code is the gate (D-34); merge_gate.py gives the mechanical verdict.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
