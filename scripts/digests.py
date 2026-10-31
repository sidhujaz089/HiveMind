#!/usr/bin/env python3
"""Write digests of the corpus files Chat depends on, from a process that is not the server.

    python3 scripts/digests.py            report drift against the recorded digests
    python3 scripts/digests.py --write    record the current digests

WHY A SIDECAR AND NOT THE DIGEST THE SERVER ALREADY RETURNS (t056)
------------------------------------------------------------------
`mcp:read` returns a server-computed `sha256` alongside the content. `PROTOCOL.txt` already
flags the limit and it is worth restating exactly: **the digest arrives in the same response
as the bytes it certifies.** A server that truncated and then hashed the truncated bytes
would produce a digest that matches perfectly. Same-source verification is not verification.

This file is the independent witness. It is written by Code, at commit time, by a different
process at a different moment, and committed to git. A reader can fetch a file over the
bridge, fetch this sidecar, and compare — and the two numbers have no common point of
failure except the disk itself.

**What it does NOT prove.** It cannot tell you the tunnel is intact if the tunnel corrupts
both requests identically, and it cannot help if the disk is wrong. It closes the case where
the *read path* damages content, which is the case that was open.

WHAT WAS ALREADY PROVEN, SO THIS IS NOT OVERSOLD (2026-08-25)
--------------------------------------------------------------
A 702,372-byte read over the loopback endpoint was compared against the file on disk — not
against the server's own claim — and came back byte-identical, digest and length. So the
**server half is proven.** The untested leg is `cloudflared` between Anthropic and this box,
and it cannot be tested from here: the WAF blocks everything outside `160.79.104.0/21` by
design, which is the control working. Only Chat sits on the far side, and Chat has no
byte-length primitive. This sidecar is how that leg gets closed, by Chat rather than by me.

THE SECOND PROPERTY, WHICH CHAT FOUND WITHOUT BEING ASKED
----------------------------------------------------------
The ledger census compares a file against a canonical element list, and that list lives in
`chat/PROTOCOL.txt` — **an unverified file**. Chat's fixture C removed four whole sections
from a ledger while leaving every remaining declared count correct and the foot intact; it is
caught only by that external list. Chat then observed the obvious next step nobody had:

> If a fixture-C-shaped defect ever hit PROTOCOL.txt's own element list -- dropping
> "9 Active frameworks" -- then a ledger missing section 9 would census clean against a list
> missing section 9. The check and the checked would agree, and be wrong together.

`PROTOCOL.txt` is in this manifest for that reason. It has no integrity property of its own
and now it has one: a change to it that nobody recorded shows up as drift.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SIDECAR = REPO / "workspace" / "corpus-digests.json"

# The files a session's correctness actually rests on. Not the whole corpus: a manifest that
# churns on every transcript is a manifest nobody reads, and these are the ones where being
# quietly wrong is expensive.
WATCHED = [
    "chat/PROTOCOL.txt",
    "chat/projects/family/State_Ledger_Family_Project.txt",
    "chat/projects/work/MMSP-State-Ledger.txt",
    "chat/projects/jshc/State_Ledger_Entrepreneur_Project.txt",
    "chat/projects/family/INSTRUCTIONS.txt",
]


def digest(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def current():
    out = {}
    for rel in WATCHED:
        p = REPO / rel
        out[rel] = ({"sha256": digest(p), "bytes": p.stat().st_size}
                    if p.is_file() else None)
    return out


class CorruptSidecar(Exception):
    """The witness exists and cannot be read. Not the same as no witness yet."""


def recorded():
    """{} when ABSENT; raises when CORRUPT. Those are different states.

    Both returned {} before, so both produced rc=0 and "nothing was checked; this is not a
    pass" -- and session_end.py treats rc=0 as no blocker. A missing sidecar is a first-run
    state. A corrupt sidecar is a BROKEN INTEGRITY WITNESS, and it read as a repository that
    simply had not run --write yet. The vacuous-pass lesson had landed on "checked vs not
    checked" and not on "not checked because new vs not checked because broken"."""
    if not SIDECAR.is_file():
        return {}
    try:
        return json.loads(SIDECAR.read_text()).get("files", {})
    except (json.JSONDecodeError, OSError) as e:
        raise CorruptSidecar(f"{SIDECAR.name} exists but cannot be read: {e}") from e


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true",
                    help="record the current digests as the new baseline")
    a = ap.parse_args(argv)

    now = current()
    try:
        was = recorded()
    except CorruptSidecar as e:
        # --write is the remedy, so let it through: you are replacing the broken witness.
        if not a.write:
            print(f"  [FAIL] {e}")
            print("         A corrupt sidecar is a BROKEN witness, not a first run. It was "
                  "reported\n         as 'nothing was checked', which reads as a repository "
                  "that has not run\n         --write yet, and rc=0 does not block "
                  "session_end.py.")
            print("         fix: inspect it, then `python3 scripts/digests.py --write`")
            return 1
        was = {}

    # A sidecar and a tree that share no comparable file is not a match -- it is an empty
    # intersection. Found by the local pair 2026-09-01, alongside the CORRUPT case above:
    # absent, corrupt, and nothing-in-common are three different states and only one is a pass.
    if not a.write:
        examined = sum(1 for rel in WATCHED
                       if now.get(rel) is not None and was.get(rel) is not None)
        if examined == 0:
            print("  [SKIP] 0 watched file(s) could be compared -- both sides are absent.")
            print("         Nothing was checked; this is not a pass.")
            return 0

    if a.write:
        SIDECAR.parent.mkdir(parents=True, exist_ok=True)
        SIDECAR.write_text(json.dumps(
            {"note": "Written by scripts/digests.py, NOT by the MCP server. Compare a "
                     "file fetched over the bridge against these numbers: the digest the "
                     "server returns arrives with the content it certifies and cannot "
                     "detect a read path that truncates and then hashes.",
             "files": now}, indent=1) + "\n", encoding="utf-8")
        print(f"recorded {sum(1 for v in now.values() if v)} digest(s) "
              f"-> {SIDECAR.relative_to(REPO)}")
        return 0

    if not was:
        print("  [SKIP] no sidecar recorded yet — run with --write")
        print("         Nothing was checked; this is not a pass.")
        return 0

    problems = []
    for rel in WATCHED:
        n, w = now.get(rel), was.get(rel)
        if w is None and n is None:
            continue
        if n is None:
            problems.append(f"{rel}: recorded but MISSING from disk")
        elif w is None:
            problems.append(f"{rel}: on disk but not recorded — run --write")
        elif n["sha256"] != w["sha256"]:
            problems.append(f"{rel}: changed since it was recorded "
                            f"({w['bytes']} -> {n['bytes']} bytes)")
    for p in problems:
        print(f"  [DRIFT] {p}")
    if problems:
        print("\n  `chat/` has no git history (D-22), so drift here is the only signal that")
        print("  one of these files changed. If the change was intended, re-record it:")
        print("      python3 scripts/digests.py --write")
        return 1
    print(f"  [PASS] {len([v for v in now.values() if v])} watched file(s) "
          f"match their recorded digests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
