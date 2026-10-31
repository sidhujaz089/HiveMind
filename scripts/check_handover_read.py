#!/usr/bin/env python3
"""Did this session actually READ the handover, or only get shown that one exists?

    python3 scripts/check_handover_read.py            check the live session
    python3 scripts/check_handover_read.py --transcript FILE

WHY A PRINT IS NOT AN ENFORCEMENT
---------------------------------
`session_start.py` names the newest `workspace/HANDOVER-*.md` and prints its first lines, and
`AGENTS.md` says to read it in full. Nothing checked. A rule enforced by asking politely is not
enforced -- this repository's own words -- and the handover is the one document written
specifically so the next session does not repeat the last one's work.

WHAT COUNTS AS EVIDENCE, AND WHY IT IS THIS
-------------------------------------------
The session transcript records every tool call. Reading the handover leaves a trace there: a
Read of the file, or a shell command naming it. Nothing else is proof -- least of all the
session saying it read it, which is the assertion this check exists to stop being taken on
trust.

The same applies to the tail of the previous session (`transcripts.py tail`), which carries the
granular detail a handover summarises away.

THIS CHECK IS DELIBERATELY A WARNING AT SESSION END, NOT A BLOCKER
-----------------------------------------------------------------
Some sessions are one question long and have no business reading a handover. A blocker there
would be a check people learn to skip (AGENTS.md rule 9). It fails only when the session did
real work -- committed something -- without ever opening the handover, which is exactly the
case that costs a repeat.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROJECTS = Path.home() / ".claude" / "projects"


def newest_transcript():
    """The transcript being written now: this session's own, else the newest .jsonl for this
    working directory.

    BY SESSION ID FIRST (2026-09-25). Claude Code files a transcript under the directory the
    session was LAUNCHED from, so a session started in ~ writes to -home-user/ even when it
    works in this repository. Looking only in this repo's directory read a transcript from
    three days earlier and reported "committed work without ever opening the handover" for a
    session that had read it first thing.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if sid:
        own = sorted(PROJECTS.glob(f"*/{sid}.jsonl"))
        if own:
            return own[0]
    d = PROJECTS / str(REPO).replace("/", "-")
    if not d.is_dir():
        return None
    files = sorted(d.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    return files[0] if files else None


def newest_handover():
    hs = sorted((REPO / "workspace").glob("HANDOVER-*.md"))
    return hs[-1] if hs else None


def evidence(transcript, handover):
    """(saw_handover, saw_tail). Substring search over the transcript's own text."""
    try:
        text = transcript.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    name = handover.name if handover else "HANDOVER-"
    saw_h = name in text or "HANDOVER-" in text
    saw_t = "transcripts.py tail" in text or "transcripts.py\\\", \\\"tail" in text
    return saw_h, saw_t


def committed_this_session():
    """Did this session do real work? A session with no commit owes nobody a handover read."""
    try:
        r = subprocess.run(["git", "log", "--since=12 hours ago", "--oneline"],
                           cwd=str(REPO), capture_output=True, text=True, timeout=30)
        return len([l for l in r.stdout.splitlines() if l.strip()])
    except Exception:                                        # noqa: BLE001
        return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--transcript", default="")
    # Q4'S AUDIT, 2026-09-03: "the test is non-deterministic: it passes or fails based on when
    # it is run relative to the last commit." Correct -- the test asserted a FAILURE that only
    # happens when git reports recent work, so on a quiet day the test failed for a reason
    # that had nothing to do with the check. The precondition is now an input.
    ap.add_argument("--assume-commits", type=int, default=None,
                    help="treat the session as having done this much work (tests only)")
    a = ap.parse_args()

    t = Path(a.transcript) if a.transcript else newest_transcript()
    h = newest_handover()
    if h is None:
        print("  No handover exists yet, so there was nothing to read. Not a pass -- "
              "nothing was checked.")
        return 0
    if t is None or not t.is_file():
        print("  No session transcript found, so this could not be checked. That is not a "
              "pass: a session that skipped the handover looks identical from here.")
        return 0

    saw_h, saw_t = evidence(t, h)
    commits = a.assume_commits if a.assume_commits is not None \
        else committed_this_session()
    print(f"  handover : {h.relative_to(REPO)}")
    print(f"  opened   : {saw_h}")
    print(f"  tail read: {saw_t}   (python3 scripts/transcripts.py tail)")
    print(f"  commits in the last 12h: {commits}")

    if saw_h:
        print("  ok -- the handover was opened in this session.")
        if not saw_t:
            print("  NOTE: the previous session's tail was not read. The handover is a "
                  "summary;\n        the tail is where the half-finished thing and the last "
                  "correction live.")
        return 0
    if commits == 0:
        print("  No commits yet, so this session has not done work that could repeat the "
              "last one's.\n  Nothing failed -- but read it before you start.")
        return 0
    print("  FAIL: this session committed work without ever opening the handover.")
    print(f"        {h.relative_to(REPO)} is the one document written so that the next")
    print("        session does not repeat the last one's work, and it was not read.")
    print("        Read it now -- what you just did may already be recorded there.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
