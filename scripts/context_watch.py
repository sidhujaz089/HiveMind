#!/usr/bin/env python3
"""Stop the session before the context window ends it for you.

    python3 scripts/context_watch.py            # report and exit
    python3 scripts/context_watch.py --watch    # block until the threshold, then exit 2

ARM IT SO THAT IT CAN REACH YOU. This matters more than the threshold.

`--watch` blocks and then exits. Whether that exit is a WARNING or a line in a file nobody
opens depends entirely on how it was started. The first version of this guard was armed with
`nohup ... > ctxwatch.log`, and it would have crossed 90%, written the banner into a 0-byte
log, and exited in silence -- a guard reporting to a place nobody reads, which is the same
defect it exists to prevent.

Start it as a HARNESS BACKGROUND TASK (`run_in_background`), not with `nohup` or `&`. The
harness delivers a notification when a background command exits, and that notification
arrives mid-turn -- which is the only way an interruption reaches a session that is busy.
That is the whole mechanism. Jas: *"you can't start in a loop if it closes it."*

WHY (2026-08-26)
----------------
A compaction fired mid-task. The thread did not end, but in practice it may as well have:
the summary is not the conversation, and the session that came out the other side drifted
off what it had been doing -- it left a probe round to potter about in the backlog, and had
to look its own memories up to get back. Jas: *"In practice there is no difference between
what happened and a new session."*

The cost is not the tokens. It is that a compaction arrives UNANNOUNCED and takes the
handover with it. A session that knows it is near the end can spend its last tokens on the
things that survive -- lessons, a check, a patch, a handover note -- instead of losing them
mid-sentence.

THE THRESHOLD IS MEASURED, NOT GUESSED
--------------------------------------
Every assistant turn in the session transcript carries `message.usage`. Summing
`input_tokens + cache_read_input_tokens + cache_creation_input_tokens` gives the real
occupancy of the window at that turn. In this session the numbers were:

    peak before compaction   997,646
    immediately after         60,412

So the window is ~1,000,000 and it compacts on touching it. The default here fires at 90%,
leaving roughly 100k -- enough for a wind-down, not enough to start something.

Both numbers are from ONE observation and are overridable. If the window changes, this
number is wrong, and it is better to say so here than to have it read as a constant.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

SESSIONS = Path.home() / ".claude" / "projects"
LIMIT = 1_000_000
THRESHOLD = 0.90
NOTICE = 0.75           # one quiet line, while there is still room to change course


def newest_session(root=SESSIONS):
    """Most recently modified .jsonl anywhere under root.

    NOT the current session, and do not use it as one. On the FIRST prompt of a new
    session this session's transcript is empty or not yet on disk, so the newest file
    is the session that just ended -- and that one is near the limit by construction,
    because being near the limit is why it ended. See `this_session()`.
    """
    files = [f for f in root.rglob("*.jsonl") if f.is_file()]
    return max(files, key=lambda f: f.stat().st_mtime) if files else None


def this_session(payload):
    """The transcript of the session that is asking. The only trustworthy source.

    The harness hands every UserPromptSubmit hook a JSON object on stdin carrying
    `transcript_path` for THIS session. That is the source of truth, and the guard
    ignored it for a `newest_session()` mtime guess.

    THE COST (2026-09-02). Every new session opened with the full-stop banner. The
    number was real -- 896,527 -- and belonged to the PREVIOUS session, 1,327 turns
    deep, whose transcript was still the newest file at the moment the new session's
    first prompt fired. The new session was at 61,746. So a guard whose entire purpose
    is to say "stop, you are nearly out" said it to sessions that had just started,
    every time, and the correct response to it -- wind down, do not begin anything --
    is the most expensive possible response to be wrong about.

    Jas: *"This happens every session. Go to the source of truth for usage."*

    Returns None when stdin gives nothing usable. None means DO NOT GUESS: a guard
    that reports a number it cannot attribute to the asking session is how this bug
    worked. Silence is the correct output.
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("transcript_path")
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_file() else None


def occupancy(path):
    """(tokens, turns) at the latest assistant turn that reported usage."""
    latest, turns = 0, 0
    for line in path.open(encoding="utf-8", errors="replace"):
        try:
            d = json.loads(line)
        except Exception:
            print(f"  NOTE: unreadable transcript line in {path.name} -- not counted", file=sys.stderr)
            continue
        u = (d.get("message") or {}).get("usage") or {}
        if not u:
            continue
        turns += 1
        latest = (u.get("input_tokens", 0)
                  + u.get("cache_read_input_tokens", 0)
                  + u.get("cache_creation_input_tokens", 0))
    return latest, turns


BANNER = """
=========================================================================
  CONTEXT AT {pct:.0f}% -- {tok:,} of {lim:,} tokens

  STOP what you are doing. Do not start anything new. A compaction from
  here takes your handover with it, and the session that follows will
  drift off the task -- that is the observed failure, not a theory.

  Spend what is left on what SURVIVES:

    1. docs/LESSONS.md   -- what this session learned, with the evidence
    2. a check or a test -- so the lesson cannot be forgotten by the next one
    3. the handover      -- what is running, what is half-done, what is
                            waiting on Jas, and the reasoning behind any
                            judgement call the next session inherits
    4. commit and push   -- an uncommitted fix does not survive anything

  Then tell Jas the session is winding down and why.

  If a /loop is running, STOP IT. A loop is a schedule, not a mandate --
  it survives a compaction while the judgement behind it does not, and
  that is how a session once wandered off a probe round into the backlog.
=========================================================================
"""


RETIRED = """
  context_watch.py --watch is RETIRED. It is not armed and must not be.

  The guard runs as a UserPromptSubmit hook, registered in .claude/settings.json.
  It is already active on every message -- a quiet line at 75%, the full stop at
  90%. There is nothing to arm and nothing to remember.

  Both long-running forms failed silently before the hook replaced them:
    nohup + logfile     -- banner written into a file nobody opens
    harness background  -- killed with exit 144 after a few minutes, unreported,
                           after a session had already said it was armed

  A guard BELIEVED to be armed is worse than no guard, because nobody thinks
  about it again. See L-122 and AGENTS.md, "Starting a session".

  If you got here from an instruction telling you to arm this, that instruction
  is stale -- please fix it where you found it.
"""


def hook(limit=LIMIT, at=THRESHOLD, notice=NOTICE, stdin=None):
    """Run every turn, from a UserPromptSubmit hook. Never raises, never blocks.

    THE LONG-RUNNING WATCHER DIED. `--watch` was armed as a background task so its exit
    would notify mid-turn, and the harness killed it with 144 after a few minutes -- no
    output, no warning. Worse than useless: a guard believed to be armed is one nobody
    thinks about again.

    A hook has no process to reap. It runs on the prompt, costs 0.03s on a 10 MB
    transcript, and writes into the context directly. Anything it prints is seen.

    IT MEASURES THE ASKING SESSION, NOT THE NEWEST FILE. It reads `transcript_path` from
    the hook payload on stdin. It used to take the most recently modified transcript in
    ~/.claude/projects, which on a new session's first prompt is the session that just
    ended -- so every new session opened with the full-stop banner carrying the dead
    session's occupancy. See `this_session()` for the numbers.

    Fail-safe by construction: every path returns 0 and prints nothing on error, and a
    payload it cannot read produces SILENCE rather than a number from somewhere else.
    A guard that can block Jas from typing is a worse failure than the one it guards
    against -- and a guard that cries wolf on turn one is one he learns to ignore.
    """
    try:
        try:
            payload = json.load(stdin or sys.stdin)
        except Exception:
            payload = None
        p = this_session(payload)
        if not p:
            return 0            # cannot attribute a number to THIS session -> say nothing

        # SAY NOTHING ON A SESSION'S FIRST PROMPT. Attributing to the asking session fixed
        # the 2026-09-02 bug where a NEW session read the PREVIOUS one's transcript. It does
        # not fix the fork: a forked session's transcript legitimately contains the parent's
        # turns, so occupancy reads high on turn one and the banner tells a session with no
        # work behind it to wind down. That is the most expensive thing to be wrong about,
        # and Jas has had to correct it more than once.
        #
        # Jas, 2026-09-07: *"the next session needs no info on how full this one is and it
        # can find its own session context amount."* So the count is of THIS HOOK'S OWN
        # FIRINGS for this session id -- not of turns in a transcript it may have inherited.
        # First firing: silent. From the second onwards the number describes work this
        # session actually did.
        seen = None
        try:
            sid = str((payload or {}).get("session_id") or "")
            if sid:
                d = Path.home() / ".cache" / "context_watch"
                d.mkdir(parents=True, exist_ok=True)
                marker = d / (re.sub(r"[^A-Za-z0-9_.-]", "", sid)[:120] or "unknown")
                seen = marker.exists()
                if not seen:
                    marker.write_text("1")
        except Exception:
            seen = None         # cannot tell -> fall through and report, as before
        if seen is False:
            return 0            # this session's first prompt: it has done nothing yet

        tok, _ = occupancy(p)
        pct = tok / limit
        if pct >= at:
            print(BANNER.format(pct=pct * 100, tok=tok, lim=limit))
        elif pct >= notice:
            # .0f rounded 89.65% to "90%" in a line ending "the full stop is at 90%",
            # which reads as a contradiction. Truncate: never round a warning UP into
            # the tier above it.
            import math
            print(f"[context {math.floor(pct*1000)/10:.1f}%] {tok:,} of {limit:,}. Past {notice*100:.0f}% — "
                  f"finish what is open and start the wind-down rather than beginning "
                  f"anything new. The full stop is at {at*100:.0f}%.")
    except Exception as e:
        # a broken guard must never cost Jas a prompt
        print(f"  NOTE: context guard failed ({e}) -- no reading this turn", file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true",
                    help="block until the threshold is crossed, then exit 2")
    ap.add_argument("--every", type=int, default=60, help="seconds between polls")
    ap.add_argument("--limit", type=int, default=LIMIT)
    ap.add_argument("--at", type=float, default=THRESHOLD)
    ap.add_argument("--session", default=None)
    ap.add_argument("--allow-retired-watch", action="store_true",
                    help="run the retired blocking watcher anyway. For the test that proves "
                         "the refusal, and for nothing else.")
    ap.add_argument("--hook", action="store_true",
                    help="UserPromptSubmit mode: print nothing until the notice tier, then "
                         "the warning, and ALWAYS exit 0 so a broken guard can never block "
                         "Jas's prompt.")
    a = ap.parse_args()

    if a.hook:
        return hook(a.limit, a.at)

    # --watch IS RETIRED, AND REFUSING IS THE POINT. Two long-running forms were tried and
    # both failed SILENTLY: nohup wrote the banner into a file nobody opens, and the harness
    # background task was killed with exit 144 after minutes, after the session had already
    # reported it armed. The hook replaced it (L-122).
    #
    # This refusal exists because retiring it was not enough. session_start.py went on
    # printing "arm the wind-down guard" for hours afterwards, and the next session obeyed --
    # in the nohup form -- while an orphan watcher from the previous session was still
    # running. A stale instruction outlives the fix that superseded it, so the retired path
    # has to say no for itself rather than trust every caller to have read the news.
    if a.watch and not a.allow_retired_watch:
        print(RETIRED, file=sys.stderr)
        return 3

    while True:
        path = Path(a.session) if a.session else newest_session()
        if not path or not path.is_file():
            print("no session transcript found", file=sys.stderr)
            return 1
        tok, turns = occupancy(path)
        pct = tok / a.limit
        if pct >= a.at:
            print(BANNER.format(pct=pct * 100, tok=tok, lim=a.limit), flush=True)
            return 2
        if not a.watch:
            print(f"  context {tok:,} / {a.limit:,} = {pct * 100:.1f}%  "
                  f"({turns} turns)  fires at {a.at * 100:.0f}%")
            return 0
        time.sleep(a.every)


if __name__ == "__main__":
    sys.exit(main())
