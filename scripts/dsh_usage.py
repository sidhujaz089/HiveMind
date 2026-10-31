#!/usr/bin/env python3
"""Read THIS DSH session's own context occupancy, from the harness session file.

    python3 scripts/dsh_usage.py                    this session ($DSH_SESSION_ID)
    python3 scripts/dsh_usage.py --session <id>
    python3 scripts/dsh_usage.py --json

WHY. The dsh-context-window skill measured 2026-09-18 that a DSH session had no
self-service occupancy number: the UI bar, or asking Jas. The harness DOES record
it though -- ~/.dsh/sessions/<workdir-slug>/<session-id>/session.v3.jsonl.zstd
carries a request/context record (model + exact context window) and a usage object
per request (input/output/total/cacheRead tokens). This reads those records
instead of estimating, so the number is the harness's own. Measured 2026-09-18:
the window field (262144) agrees with `ollama ps` CONTEXT for q4_K_M, which the
skill had already verified against the UI the same day.

WHICH RECORD WINS. The LAST request/context record (model can change under a
session), and the usage object with the HIGHEST seq (an early request's usage is
the OLDEST context, not the current one). Both rules pinned in
scripts/tests/test_dsh_usage.py.

OUTSIDE A DSH SESSION ($DSH_SESSION_ID unset) it says so and exits 2 -- a reader
that guesses is the L-148 mistake this repo already has a lesson for.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _walk_usage(o):
    """The usage object (the dict holding totalTokens) anywhere inside o, else None."""
    if isinstance(o, dict):
        if "totalTokens" in o:
            return o
        for v in o.values():
            found = _walk_usage(v)
            if found:
                return found
    elif isinstance(o, list):
        for v in o:
            found = _walk_usage(v)
            if found:
                return found
    return None


def parse_stream(text):
    """(window, model, usage_seq, usage) from a decompressed session record stream.

    The window/model come from the last request/context record seen (by seq); the
    usage from the highest-seq record that carries a usage object. A stream with
    usage but no context record yields (None, None) for both -- invented windows
    are how the 191K-vs-1M misread happened 2026-09-18.
    """
    window = model = None
    ctx_seq = -1
    usage = None
    usage_seq = -1
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        # A record in the harness stream that is not JSON is STATE, not noise:
        # the file is written per-line and a bad line means a torn write we must
        # surface (swallowed-exception), not a line the reader is entitled to skip.
        try:
            r = json.loads(line)
        except ValueError as e:
            raise ValueError(f"malformed record at line {n}: {e}") from None
        if not isinstance(r, dict):
            continue
        seq = r.get("seq", -1)
        if r.get("type") == "request/context":
            d = r.get("data") or {}
            if isinstance(d, dict) and "contextWindow" in d and seq >= ctx_seq:
                ctx_seq = seq
                window = d.get("contextWindow")
                model = d.get("model")
        u = _walk_usage(r)
        if u and seq >= usage_seq:
            usage_seq = seq
            usage = u
    return window, model, usage_seq, usage


def occupancy(used, window):
    """Used tokens as a percentage of the window, or None when either is unknown."""
    if not isinstance(used, (int, float)) or not isinstance(window, (int, float)):
        return None
    if window <= 0:
        return None
    return round(100.0 * used / window, 1)


def find_session_file(session_id, dsh_home):
    base = Path(dsh_home) / "sessions"
    if not base.is_dir():
        return None
    matches = sorted(base.glob(f"*/{session_id}/session.v3.jsonl.zstd"),
                     key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def decompress(path):
    exe = shutil.which("zstd")
    if not exe:
        raise SystemExit("zstd CLI not found -- cannot decompress the session file")
    try:
        r = subprocess.run([exe, "-dc", str(path)], capture_output=True, text=True,
                           timeout=120)
    except subprocess.TimeoutExpired:
        raise SystemExit(f"zstd took longer than 120 s on {path} -- the file is likely "
                         "corrupt or the machine is under memory pressure") from None
    if r.returncode != 0:
        raise SystemExit(f"zstd failed on {path}: {r.stderr.strip()}")
    return r.stdout


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--session", default=None, help="session id (default $DSH_SESSION_ID)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    sid = a.session or os.environ.get("DSH_SESSION_ID")
    if not sid:
        raise SystemExit("not inside a DSH session (no $DSH_SESSION_ID) and no --session "
                         "given -- refusing to guess")
    dsh_home = os.environ.get("DSH_HOME") or str(Path.home() / ".dsh")
    path = find_session_file(sid, dsh_home)
    if path is None:
        raise SystemExit(f"no session file for {sid} under {dsh_home}/sessions "
                         "-- the session may not be persisted yet")

    window, model, seq, usage = parse_stream(decompress(path))
    used = usage.get("totalTokens") if usage else None
    pct = occupancy(used, window)

    if a.json:
        print(json.dumps({"session": sid, "model": model, "window": window,
                          "used": used, "cacheRead": (usage or {}).get("cacheReadTokens"),
                          "occupancy_pct": pct, "record_seq": seq, "source": str(path)}))
    else:
        print(f"session:    {sid}")
        print(f"model:      {model or '(no request/context record yet)'}")
        print(f"window:     {window if window is not None else 'unknown (no context record)'} "
              f"tokens")
        if usage:
            print(f"used:       {used} tokens  (input {usage.get('inputTokens')}, "
                  f"output {usage.get('outputTokens')}, cache-read "
                  f"{usage.get('cacheReadTokens')})")
            print(f"occupancy:  {pct}% of context" if pct is not None
                  else "occupancy:  unknown (window not recorded)")
        else:
            print("used:       (no usage record yet)")
        print(f"source:     {path}  (record seq {seq})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
