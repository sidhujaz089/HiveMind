#!/usr/bin/env python3
"""t695: the context guard must measure the window THIS session declares.

context_watch.py divides by a constant, LIMIT = 1_000_000 (context_watch.py:56), whose own
docstring says the number comes from ONE observation and that if the window changes the number
is wrong. scripts/dsh_usage.py:14 records the harness's own measured DSH window as 262,144,
and scripts/dsh_usage.py:56 names inventing a window as how the 191K-vs-1M misread happened
2026-09-18. A guard measuring a 262,144-token session against 1,000,000 says nothing at
240,000 tokens -- 91.6% full, 24.0% by the constant -- which is the measured instance in
workspace/rca/444e71df39c1.md, where the session said "My context is nearly full" with the
guard silent.

Every fixture here is built inside a TemporaryDirectory: its own transcript, its own HOME (so
the marker file the hook writes cannot touch the real ~/.cache), no model, no server, no live
session.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

# The harness's own measured window (scripts/dsh_usage.py:14), deliberately distinct from the
# guard's 1,000,000 so a build that keeps dividing by the constant cannot pass.
WINDOW = 262_144
FULL_STOP = 240_000        # 91.6% of 262,144; 24.0% of 1,000,000 -> today: silence
NOTICE_AT = 210_000        # 80.1% of 262,144; 21.0% of 1,000,000 -> today: silence


def transcript(path, tokens):
    """One assistant turn reporting `tokens`, in the shape occupancy() reads: the
    usage input + cache_read + cache_creation sum."""
    rec = {"type": "assistant", "message": {"role": "assistant", "usage": {
        "input_tokens": tokens, "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0, "output_tokens": 7}}}
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return path


def run_twice(cw, homedir, tpath, sid, window):
    """Fire cw.hook twice for one session id; return [(rc, stdout), ...].

    The FIRST firing is that session's first prompt, which the guard must answer with
    silence; the SECOND is the first real report. HOME points into the temp dir so the
    marker file the hook writes lands there, never in the real ~/.cache. `declared_window`
    is stubbed so the test owns the window the session publishes.
    """
    real_home = os.environ.get("HOME")
    real_impl = getattr(cw, "declared_window", None)
    cw.declared_window = lambda session_id, dsh_home=None: window
    os.environ["HOME"] = str(homedir)
    seen = []
    try:
        for _ in range(2):
            buf = io.StringIO()
            payload = {"session_id": sid, "transcript_path": str(tpath)}
            with contextlib.redirect_stdout(buf):
                rc = cw.hook(stdin=io.StringIO(json.dumps(payload)))
            seen.append((rc, buf.getvalue()))
    finally:
        if real_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = real_home
        if real_impl is None:
            del cw.declared_window
        else:
            cw.declared_window = real_impl
    return seen


def main():
    fails = []
    import context_watch as cw

    def ck(label, got, want):
        ok = got == want
        print(f"  [{'ok' if ok else 'BAD'}]    {label} -> {got!r}")
        if not ok:
            fails.append(f"{label}: expected {want!r}, got {got!r}")

    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "home"
        home.mkdir(parents=True)

        # A session at 240,000 tokens: the guard must fire, and say the real number.
        tpath = transcript(Path(td) / "full.jsonl", FULL_STOP)
        out = run_twice(cw, home, tpath, "t695-full-stop", WINDOW)
        ck("a session's first firing stays silent (it has done nothing)", out[0][1], "")
        ck("the guard never blocks the prompt (exit 0)", out[1][0], 0)
        ck("240,000 tokens of a 262,144 window fires the full stop",
           "STOP what you are doing" in out[1][1], True)
        ck("the banner quotes the session's OWN window", "of 262,144 tokens" in out[1][1], True)
        ck("the banner is no longer reading 1,000,000", "1,000,000" in out[1][1], False)

        # No window published: fall back to today's number, never invent a smaller one.
        out2 = run_twice(cw, home, tpath, "t695-no-window", None)
        ck("an unknown window says nothing at 240,000 tokens", out2[1][1], "")
        ck("an unknown window fires no notice", out2[0][1], "")

        # A payload that carries no session id: silence, not a crash, not a guess.
        out3 = run_twice(cw, home, tpath, "", None)
        ck("a payload with no session_id does not fire", out3[1][1], "")

        # The quiet notice tier, with the real denominator in the line it prints.
        npath = transcript(Path(td) / "notice.jsonl", NOTICE_AT)
        out4 = run_twice(cw, home, npath, "t695-notice", WINDOW)
        ck("210,000 of 262,144 is past the notice tier", "context 80.1%" in out4[1][1], True)
        ck("the notice names the real window", "262,144" in out4[1][1], True)
        ck("the notice is not the full stop", "STOP what you are doing" in out4[1][1], False)
        ck("the notice still names the full stop's threshold",
           "full stop is at 90%" in out4[1][1], True)

    # window_from_text reads the window out of the session's own stream: the LAST
    # request/context record by seq, the rule scripts/dsh_usage.py already enforces.
    stream = "\n".join(json.dumps(r) for r in [
        {"type": "request/context", "seq": 7,
         "data": {"model": "q4_K_M", "contextWindow": WINDOW}},
        {"type": "request/context", "seq": 5, "data": {"model": "old", "contextWindow": 1000}},
    ]) + "\n"
    ck("the highest-seq context record sets the window", cw.window_from_text(stream), WINDOW)
    ck("a stream with no context record publishes no window", cw.window_from_text(""), None)

    for x in fails:
        print("FAIL:", x)
    print("test_context_watch_window:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
