#!/usr/bin/env python3
"""Move Chat's per-turn captures from the staging buffer into the corpus.

    python3 scripts/sweep_captures.py            what is waiting, and what it would do
    python3 scripts/sweep_captures.py --apply    copy, verify, then remove the source

WHY THE BUFFER EXISTS AT ALL (Chat's handover section 4)
--------------------------------------------------------
Chat appends one JSON object per turn to `workspace/chat-live/`. It cannot write to `chat/`
at all -- every attempt refuses as `path-read-only` -- and that refusal is deliberate and
permanent: **the corpus is what Chat remembers FROM, and a memory that can rewrite its own
record is not a memory.** So Chat writes to staging and Code moves it across. Chat asked
explicitly that this not be simplified into a direct corpus append.

WHY IT IS URGENT RATHER THAN TIDY
---------------------------------
`workspace/chat-live/` is gitignored -- D-22 applied to a new path, because the family project
runs through Chat and that buffer holds the same material. Gitignored means **no history and
no GitHub copy**. Until a capture is swept into `chat/` and `chat/` is backed up to the USB,
a live session exists in exactly one place on one disk.

COPY, VERIFY, THEN REMOVE -- IN THAT ORDER
------------------------------------------
Never move-then-check. `chat/` has no git rollback (D-22), so a half-completed move is
unrecoverable. Every file is copied, re-read, and compared by sha256 before the source is
touched. If the hash does not match, the source stays and the run reports a failure.
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIVE = REPO / "workspace" / "chat-live"
DEST = REPO / "chat" / "captures"


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def describe(p):
    """Turn count and session slug, for a report that says something useful."""
    if p.suffix != ".jsonl":
        return f"{p.stat().st_size:,} bytes"
    turns = roles = 0
    sessions = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            print(f"  NOTE: unreadable line in {p} -- not counted", file=sys.stderr)
            continue
        turns += 1
        if o.get("role"):
            roles += 1
        if o.get("session"):
            sessions.add(o["session"])
    s = f"{turns} turn(s)"
    if sessions:
        s += f", session(s): {', '.join(sorted(sessions))[:60]}"
    return s


def pending():
    if not LIVE.is_dir():
        return []
    return sorted(f for f in LIVE.iterdir()
                  if f.is_file() and not f.name.startswith("."))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="actually sweep; without it this only reports")
    a = ap.parse_args()

    files = pending()
    if not files:
        print("chat-live is empty -- nothing to sweep")
        return 0

    print(f"{len(files)} file(s) in workspace/chat-live/:\n")
    for f in files:
        print(f"  {f.name}\n      {describe(f)}")
        if (DEST / f.name).exists():
            same = sha(DEST / f.name) == sha(f)
            print(f"      already in chat/captures/ and "
                  f"{'IDENTICAL — safe to remove' if same else 'DIFFERENT — will not overwrite'}")

    if not a.apply:
        print(f"\nWould copy into {DEST.relative_to(REPO)}/ then verify by sha256, then remove\n"
              "the source. Nothing has been touched. Re-run with --apply.")
        return 0

    DEST.mkdir(parents=True, exist_ok=True)
    swept = failed = 0
    for f in files:
        target = DEST / f.name
        if target.exists() and sha(target) != sha(f):
            # Two different files with one name. Refuse rather than pick a winner: the corpus
            # has no rollback and nothing here can tell which one matters.
            print(f"  REFUSED {f.name}: a different file already exists in the corpus")
            failed += 1
            continue
        before = sha(f)
        shutil.copy2(f, target)
        after = sha(target)
        if after != before:
            print(f"  FAILED {f.name}: copy does not match source; source kept")
            failed += 1
            continue
        f.unlink()
        swept += 1
        print(f"  swept {f.name}  sha256 {after[:16]} verified")

    print(f"\n{swept} swept, {failed} failed")
    if swept:
        print("These are in chat/, which is gitignored under D-22 -- so they are still ONE "
              "copy\nuntil scripts/backup_corpora.py runs against the USB.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
