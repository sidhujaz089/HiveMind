#!/usr/bin/env python3
"""Did Chat capture the session it was in?

    python3 scripts/check_captures.py            report
    python3 scripts/check_captures.py --strict   exit 1 if a gap is found

THE PROBLEM (L-086)
-------------------
Chat appends one JSON object per turn. On 2026-08-21 the `append` tool dropped out of its
loaded tool set mid-session; it wrote five turns and then could not write any more. Two turns
went uncaptured, and **the only reason anyone knows is that Chat volunteered it in prose.**

Neither end can see this alone. The server cannot distinguish *did not append* from *could
not append*. The client cannot report a failure once it has lost the means to speak. A
control that runs by the actor choosing to act cannot report its own absence -- the same
family as `L-076` and `L-039`.

THE SIGNAL THAT DOES EXIST
--------------------------
`workspace/mcp-calls.jsonl` is written by the SERVER, not by Chat. So a window in which Chat
made reads, searches or inbox_posts and appended NOTHING is visible from outside, and needs
no cooperation from the actor. That is the whole idea: stop asking the actor, and compare two
records it does not control.

WHAT A GAP IS NOT
-----------------
Not every silent window is a failure. Chat may simply not be running a captured session, and
a single read is not a conversation. So a gap is reported only when there is enough activity
to imply a session -- `MIN_CALLS` in a window -- and it is reported as *worth looking at*,
never as proof. A check that cries wolf gets ignored, which is `G10`.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CALLS = REPO / "workspace" / "mcp-calls.jsonl"
CAPTURES = [REPO / "workspace" / "chat-live", REPO / "chat" / "captures"]

WINDOW = timedelta(minutes=30)   # calls this far apart are treated as separate sessions
MIN_CALLS = 4                    # below this it is not a conversation, it is a lookup

# The `append` tool shipped 2026-08-21; its first call is in the log at 06:02:04Z. Sessions
# before that could not be captured, so flagging them is noise -- and the FIRST version of
# this check did exactly that, reporting five "gaps" that were all pre-append. Its own
# docstring warned that a check which cries wolf gets ignored (G10), which is a useful
# demonstration that writing the warning down does not prevent the mistake.
CAPTURE_POSSIBLE_FROM = datetime(2026, 8, 21, 6, 0, tzinfo=timezone.utc)


def parse(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def sessions():
    """Group Chat's bridge calls into windows of activity."""
    if not CALLS.is_file():
        return []
    rows = []
    for line in CALLS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            print(f"  NOTE: unreadable line in {CALLS} -- not counted", file=sys.stderr)
            continue
        # Only calls made by Chat over the bridge. `direct` is Code on the box.
        if not str(r.get("via", "")).startswith("oauth:"):
            continue
        at = parse(r.get("ts"))
        if at:
            rows.append((at, r.get("tool"), r.get("outcome")))
    rows.sort()

    out, cur = [], []
    for at, tool, outcome in rows:
        if cur and at - cur[-1][0] > WINDOW:
            out.append(cur)
            cur = []
        cur.append((at, tool, outcome))
    if cur:
        out.append(cur)
    return out


def captured_days():
    """Dates for which a capture file exists, from its filename."""
    days = set()
    for d in CAPTURES:
        if d.is_dir():
            for f in d.iterdir():
                if f.is_file() and len(f.name) >= 10:
                    days.add(f.name[:10])
    return days


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    groups = sessions()
    if not groups:
        print("no Chat bridge activity in the call log -- nothing to check")
        return 0

    have = captured_days()
    gaps = []
    print(f"{len(groups)} window(s) of Chat activity:\n")
    for g in groups:
        start, end = g[0][0], g[-1][0]
        tools = defaultdict(int)
        for _, tool, _ in g:
            tools[tool] += 1
        # A refused append is not a capture -- it is the exact failure this check
        # exists to detect (L-086). Count only successful appends.
        appends = sum(1 for _, tool, outcome in g if tool == "append" and outcome == "ok")
        day = start.strftime("%Y-%m-%d")
        mins = max(1, int((end - start).total_seconds() // 60))
        summary = ", ".join(f"{k}x{v}" for k, v in sorted(tools.items()))
        flag = ""
        if end < CAPTURE_POSSIBLE_FROM:
            print(f"  {start:%Y-%m-%d %H:%M}  {mins:>3}min  {len(g):>3} call(s)  {summary}"
                  f"  (before append existed)")
            continue
        if appends == 0 and len(g) >= MIN_CALLS:
            if day in have:
                flag = "  no appends, but a capture file exists for that day"
            else:
                flag = "  <- NO APPENDS and no capture file: worth looking at"
                gaps.append((day, len(g), summary))
        print(f"  {start:%Y-%m-%d %H:%M}  {mins:>3}min  {len(g):>3} call(s)  {summary}{flag}")

    if gaps:
        print(f"\n{len(gaps)} window(s) look like an uncaptured session:")
        for day, n, summary in gaps:
            print(f"  {day}: {n} calls ({summary}) and nothing appended")
        print("\nThis is a PROMPT TO LOOK, not proof. Chat may not have been running a "
              "captured\nsession. But a gap the actor cannot report is now visible from "
              "outside it, which\nis the point (L-086).")
    else:
        print("\nNo uncaptured windows found.")
    return 1 if (a.strict and gaps) else 0


if __name__ == "__main__":
    sys.exit(main())
