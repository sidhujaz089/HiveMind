#!/usr/bin/env python3
"""Every dispatcher actually writes its row. Not that it intends to -- that it does.

WHY THIS EXISTS. On 2026-09-11 ask_q16.py gained a _ledger() call and the LEDGER constant it
needs was never added: the edit script that was supposed to write the file printed its result
instead. _ledger() catches its own failure and carries on -- correct, an answer must not be
lost to a bookkeeping bug -- so every dispatch for the next hour printed one grey line and
logged nothing. It was found only because that line happened to be visible in a log tail.

A wiring bug that DEGRADES SILENTLY is the shape this whole ledger exists to catch, so it
gets a test rather than a promise. The assertion is behavioural: call the thing, count the
rows. Checking that the constant is defined would have passed the moment someone defined it
and misspelled the path.
"""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'BAD'}]    {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(f"{label} {detail}")

    import ask_q16
    import explore_loop

    check("ask_q16 knows where the ledger is", hasattr(ask_q16, "LEDGER"),
          str(getattr(ask_q16, "LEDGER", "UNDEFINED")))
    check("explore_loop knows the repo root", hasattr(explore_loop, "REPO"))

    # THE BEHAVIOURAL HALF. Redirect the ledger and prove a row lands.
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "agent-runs.jsonl"
        real, ask_q16.LEDGER = ask_q16.LEDGER, led
        try:
            ask_q16._ledger("t", {"done_reason": "timeout", "complete": False, "text": "",
                                  "trail": [{"tool_rounds": 3}], "warning": "TIMED OUT"}, 1.5)
        finally:
            ask_q16.LEDGER = real
        rows = [json.loads(l) for l in led.read_text().splitlines() if l.strip()] \
            if led.is_file() else []
        check("a dispatch writes exactly one row", len(rows) == 1, f"{len(rows)} row(s)")
        if rows:
            r = rows[0]
            # ASSERT THE VALUE. The outcome must be the RAW done_reason -- error_scan decides
            # health by enumerating the good, so translating it here would hide the outcomes
            # that matter.
            check("outcome is the raw done_reason", r.get("outcome") == "timeout",
                  repr(r.get("outcome")))
            for k in ("at", "task", "tool", "complete"):
                check(f"row carries {k}", k in r, repr(r.get(k)))

    # AND THE OUTCOME WORDS MUST NOT BE ON THE HEALTHY ALLOWLIST, or the row is written and
    # still reads as fine -- which is precisely what done_reason="stop" did for a blind run.
    try:
        import error_scan
        healthy = getattr(error_scan, "HEALTHY_STATUS", ())
        for word in ("timeout", "blind", "checkpoint-unparsed", "zero-tool-calls", "incomplete"):
            check(f"{word!r} is not treated as healthy", word not in healthy)
        check("'ok' IS treated as healthy", "ok" in healthy)
    except ImportError:
        check("error_scan importable", False, "could not import")

    print()
    if fails:
        print(f"FAIL: {len(fails)} problem(s)")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
