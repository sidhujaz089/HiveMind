#!/usr/bin/env python3
"""Run one command over many items, one at a time, and SURVIVE losing any of them.

    python3 scripts/each.py --out gate.txt --timeout 900 \
        -- python3 scripts/merge_gate.py {}  <  branches.txt
    python3 scripts/each.py --out gate.txt --timeout 900 --items a,b,c \
        -- python3 scripts/merge_gate.py {}

WHY THIS EXISTS, 2026-09-14. Jas: *"instead of just a lesson can a mechanical fixture that
pipes incrementally be made so things queue one by one instead of in bulk?"*

The failure it replaces, in full, because every part of it mattered:

    timeout 3000 python3 scripts/merge_gate.py 2>&1 | tail -60

That ran 17 branches for fifty minutes, was killed at its timeout, and produced **zero bytes**.
Three separate faults, and removing any one of them would still have lost the run:

1. `tail` EMITS NOTHING UNTIL ITS INPUT CLOSES. Fifty minutes of output sat in a pipe buffer
   and died with the process. A long job piped through `tail` has no partial results by
   construction -- it is all-or-nothing, and the "all" case is the one that does not happen.
2. ONE PROCESS FOR SEVENTEEN ITEMS. The timeout applied to the whole batch, so slow item 9
   destroyed finished items 1-8. There was no such thing as "done so far".
3. A HUNG ITEM STOPS EVERYTHING. Nothing bounded an individual item, so one that never
   returned consumed the entire budget and every item after it was never attempted.

So: one child per item, its own timeout, output appended to a FILE as it finishes, and a
hung item KILLED and SKIPPED rather than allowed to end the run. Jas asked exactly that --
*"if it dies at branch 4, can it skip 4 and go to 5?"* -- and the answer is yes, that is the
whole point.

WHAT IT WILL NOT DO. It does not truncate. Every byte a child writes reaches the file. This
file is the fixture for a session that got tired of losing output to a cap, so introducing
one here would be the joke telling itself. If a child is enormous, that is the child's
business and the file's; the tail of a big result is exactly where the failure usually is.

THE RESULT FILE IS THE ARTIFACT, which is the other half of L-105 and of
hooks/guard_process_waits.py: wait on the artifact, never on the process. An artifact cannot
match the thing looking for it and cannot be a zombie. Poll the file; when the SUMMARY line
appears the run is over.
"""
import argparse
import shlex
import subprocess
import sys
import time
from pathlib import Path

MARK = "#####"


def run_one(cmd, item, out, timeout):
    """One item. Returns its status string. NOTHING it printed is ever discarded."""
    argv = [a.replace("{}", item) for a in cmd]
    if not any("{}" in a for a in cmd):
        argv = [*cmd, item]
    started = time.time()
    with out.open("a", encoding="utf-8") as f:
        f.write(f"{MARK} {item}\n")
        f.flush()
        try:
            # stdin=DEVNULL IS LOAD-BEARING, TWICE OVER. Added 2026-09-14 when Jas asked
            # what the fix is for a process that hangs because its input never closes.
            #   1. A child that reads stdin and inherits ours BLOCKS FOREVER waiting for
            #      input nobody will send -- it burns the whole per-item timeout and looks
            #      identical to a slow job. DEVNULL gives it immediate EOF instead.
            #   2. WORSE HERE SPECIFICALLY: this script can read its OWN item list from
            #      stdin. A child inheriting that descriptor can consume the remaining
            #      items, so jobs silently vanish from the run and the count still looks
            #      right. Closing it for the children makes that impossible.
            r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                               stdin=subprocess.DEVNULL)
            # stdout and stderr BOTH, whole. A failure explains itself on stderr and that is
            # the stream a careless pipe drops first.
            if r.stdout:
                f.write(r.stdout if r.stdout.endswith("\n") else r.stdout + "\n")
            if r.stderr:
                f.write("  [stderr]\n" + (r.stderr if r.stderr.endswith("\n")
                                          else r.stderr + "\n"))
            status = "ok" if r.returncode == 0 else f"exit {r.returncode}"
        except subprocess.TimeoutExpired as e:
            # KILLED AND SKIPPED, and it says so IN the file. A silent skip would make a
            # hung item indistinguishable from one that passed with no output -- which is
            # the class of mistake this whole file is a reaction to.
            partial = (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
            if partial:
                f.write("  [partial output before the timeout, kept]\n" + partial + "\n")
            f.write(f"  TIMED OUT after {timeout}s -- killed and SKIPPED. "
                    f"The items after this one still ran.\n")
            status = f"TIMEOUT {timeout}s"
        except OSError as e:
            f.write(f"  COULD NOT RUN: {e}\n")
            status = f"could-not-run: {e}"
        f.write(f"  [{item}: {status} in {time.time() - started:.1f}s]\n")
        f.flush()
    return status


def main():
    ap = argparse.ArgumentParser(
        description="Run a command once per item, appending each result to --out as it lands.")
    ap.add_argument("--out", required=True, help="result file; appended to, never rewritten")
    ap.add_argument("--timeout", type=int, default=900, help="seconds PER ITEM (default 900)")
    ap.add_argument("--items", default=None,
                    help="comma-separated items; omit to read one per line on stdin")
    ap.add_argument("cmd", nargs=argparse.REMAINDER,
                    help="-- then the command; {} is replaced by the item, "
                         "or the item is appended if there is no {}")
    a = ap.parse_args()

    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        ap.error("no command given -- put it after --")

    if a.items:
        items = [i.strip() for i in a.items.split(",") if i.strip()]
    else:
        items = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    if not items:
        ap.error("no items given (--items, or one per line on stdin)")

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(f"{MARK} RUN of {len(items)} item(s): {shlex.join(cmd)}\n")

    results = {}
    for item in items:
        results[item] = run_one(cmd, item, out, a.timeout)

    # THE SUMMARY IS THE DONE MARKER. Poll for it instead of watching the process.
    bad = {k: v for k, v in results.items() if v != "ok"}
    with out.open("a", encoding="utf-8") as f:
        f.write(f"\n{MARK} SUMMARY {len(results) - len(bad)}/{len(results)} ok\n")
        for k, v in results.items():
            f.write(f"  {'ok  ' if v == 'ok' else 'BAD '} {k}: {v}\n")
    print(f"{len(results) - len(bad)}/{len(results)} ok -- full output in {out}")
    for k, v in bad.items():
        print(f"  BAD {k}: {v}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
