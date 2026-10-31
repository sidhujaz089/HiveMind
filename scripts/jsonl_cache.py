#!/usr/bin/env python3
"""Read an append-only JSONL file once, then only what was appended since.

WHY (2026-10-03, Jas: "it takes several seconds for any site to open up"). The Hub is one long-lived
process, and the Production board re-read workspace/dispatch-loop.jsonl (67 MB, a full queue listing
every 10-minute tick) and its siblings from the top on every load: 4,394 file reads and 89 s for one
board, 83 s of it reading and decoding. These files are only ever appended to, so a process can keep
what it parsed and read just the new bytes. A file that shrank or was replaced is read again whole.

    rows(path) -> list of parsed rows (a fresh list each call; the dicts are shared -- do not mutate)
    rows(path, torn=True) -> (rows, count of lines that did not parse)

A line without its newline yet is a write in progress and is left for the next call.
"""
import json
import os
import sys
import threading
from pathlib import Path

_lock = threading.Lock()
_cache = {}          # str(path) -> {"ino", "offset", "rows", "torn"}


def rows(path, torn=False):
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return ([], 0) if torn else []
    key = str(p)
    with _lock:
        c = _cache.get(key)
        if c is None or c["ino"] != st.st_ino or st.st_size < c["offset"]:
            c = _cache[key] = {"ino": st.st_ino, "offset": 0, "rows": [], "torn": 0}
        if st.st_size > c["offset"]:
            with open(p, "rb") as f:
                f.seek(c["offset"])
                data = f.read(st.st_size - c["offset"])
            end = data.rfind(b"\n") + 1          # only whole lines; the rest is still being written
            for line in data[:end].splitlines():
                if not line.strip():
                    continue
                try:
                    c["rows"].append(json.loads(line))
                except ValueError:
                    c["torn"] += 1
            c["offset"] += end
        out, bad = list(c["rows"]), c["torn"]
    return (out, bad) if torn else out


def latest_tick_file(ledger):
    """The dispatcher's newest FULL tick from workspace/run-logs/dispatch-tick-latest.json beside the ledger
    (rewritten every tick), or None when the file is missing or unreadable -- said on stderr if unreadable.
    Does not touch the ledger, so it is safe where parsing 67 MB is not."""
    f = Path(ledger).parent / "run-logs" / "dispatch-tick-latest.json"
    try:
        t = json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        print(f"  NOTE: {f.name} unreadable ({e}); reading the ledger's own ticks", file=sys.stderr)
        return None
    return t if isinstance(t, dict) and "skipped" in t else None


def latest_tick(ledger):
    """The newest FULL tick -- its queued and skipped lists -- or {} (t675). The latest-tick file first; ticks
    written before 2026-10-05 carry their lists in the ledger itself, so without the file the newest ledger
    tick that has a `skipped` list is used."""
    t = latest_tick_file(ledger)
    if t is not None:
        return t
    return next((r for r in reversed(rows(Path(ledger))) if r.get("event") == "tick" and "skipped" in r), {})


if __name__ == "__main__":
    for a in sys.argv[1:]:
        r, bad = rows(a, torn=True)
        print(f"{a}: {len(r)} rows, {bad} unreadable, {os.path.getsize(a)} bytes")
