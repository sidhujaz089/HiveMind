#!/usr/bin/env python3
"""Has anything Chat read changed since it read it?

    python3 scripts/check_read_staleness.py            report
    python3 scripts/check_read_staleness.py --since 2026-08-21
    python3 scripts/check_read_staleness.py --strict   exit 1 if a stale read is found

THE PROBLEM (ROADMAP G4/G5, Chat's C4, backlog item 1)
-------------------------------------------------------
Chat reads a file at the top of a session and composes anchored edits from that read at the
close, possibly hours later. If Code touched the file in between, the anchors describe a file
that no longer exists and the whole manifest aborts unapplied.

**That is the safe failure, and it happened for real on 2026-08-21.** Two of thirteen anchors
in Chat's ledger delta refused as `anchor-not-found` -- not because Chat mis-anchored, but
because the drift it was correcting had already been corrected. Nothing was damaged. But it
was invisible from Chat's side until someone evaluated the manifest, and the eleven good
operations were held behind the two stale ones.

THE SIGNAL
----------
Every `read` row in `workspace/mcp-calls.jsonl` carries the `sha256` of the bytes served, and
the server writes that row. So "what Chat read" and "what the file is now" are two independent
records, and comparing them needs nothing from Chat. This turns a refusal-on-arrival into a
warning at close.

WHAT A STALE READ IS NOT
------------------------
It is not an error. Files are supposed to change. It matters only when someone is about to
compose an edit against an old read, which is why this reports rather than fails by default,
and why `--since` exists: the useful question is almost always "did anything I read TODAY
move underneath me".
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CALLS = REPO / "workspace" / "mcp-calls.jsonl"


def sha(p):
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--since", help="only reads on or after this date (YYYY-MM-DD)")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="every read, not just the most recent per file")
    a = ap.parse_args()

    if not CALLS.is_file():
        print("no call log")
        return 0

    latest = {}
    for line in CALLS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            print(f"  NOTE: unreadable line in {CALLS} -- not counted", file=sys.stderr)
            continue
        if r.get("tool") != "read" or r.get("outcome") != "ok" or not r.get("sha256"):
            continue
        if a.since and (r.get("ts") or "")[:10] < a.since:
            continue
        # Most recent read per path: an older read being stale is expected and uninteresting.
        key = r["target"]
        if key not in latest or r["ts"] > latest[key]["ts"]:
            latest[key] = r

    if not latest:
        print("no reads to check" + (f" since {a.since}" if a.since else ""))
        return 0

    stale, gone, fresh = [], [], 0
    for path, r in sorted(latest.items()):
        f = REPO / path
        now = sha(f)
        if now is None:
            gone.append((path, r["ts"]))
        elif now != r["sha256"]:
            stale.append((path, r["ts"], r["sha256"][:12], now[:12]))
        else:
            fresh += 1

    print(f"{len(latest)} file(s) read{' since ' + a.since if a.since else ''}: "
          f"{fresh} unchanged, {len(stale)} changed, {len(gone)} now missing\n")
    for path, ts, was, now in stale:
        print(f"  CHANGED  {path}")
        print(f"           read {ts} as {was}…, now {now}…")
    for path, ts in gone:
        print(f"  MISSING  {path}  (read {ts}, file no longer present)")

    if stale or gone:
        print("\nNot an error -- files are meant to change. It matters when an edit is about\n"
              "to be composed against one of these reads. On 2026-08-21 exactly this made two\n"
              "of Chat's thirteen anchors refuse, holding the other eleven behind them.")
    return 1 if (a.strict and (stale or gone)) else 0


if __name__ == "__main__":
    sys.exit(main())
