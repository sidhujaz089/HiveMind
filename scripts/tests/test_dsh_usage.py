#!/usr/bin/env python3
"""dsh_usage: a DSH session must be able to READ ITS OWN context occupancy.

The dsh-context-window skill measured 2026-09-18 that the only self-service number
was the harness UI's bar (or Jas's word of it). The harness DOES record it, in
~/.dsh/sessions/<workdir>/<session-id>/session.v3.jsonl.zstd: a request/context
record carries the model and the exact context window, and the per-request usage
objects carry total/input/output/cacheRead tokens. This pins the reader's two
behaviours that a wrong answer on would cost a session:

  1. the window and model come from the LAST request/context record, not a guess;
  2. the occupancy comes from the LATEST usage object (highest seq), because an
     early request's usage is the oldest context, not the current one.

Both are fed as synthetic records, so the test never depends on a live session.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def main():
    import dsh_usage
    fails = []

    def ck(label, got, want):
        ok = got == want
        print(f"  [{'ok' if ok else 'BAD'}]    {label} -> {got}")
        if not ok:
            fails.append(f"{label}: expected {want}, got {got}")

    records = [
        {"type": "request/context", "seq": 5, "data": {"model": "old:tag",
                                                       "contextWindow": 1000}},
        {"type": "assistant/message", "seq": 6,
         "data": {"message": {"usage": {"inputTokens": 10, "outputTokens": 5,
                                        "totalTokens": 20, "cacheReadTokens": 0}}}},
        {"type": "request/context", "seq": 7, "data": {"model": "new:tag",
                                                       "contextWindow": 2000}},
        {"type": "assistant/message", "seq": 8,
         "data": {"message": {"usage": {"inputTokens": 100, "outputTokens": 50,
                                        "totalTokens": 400, "cacheReadTokens": 300}}}},
    ]
    text = ' {"type":"session","id":"x"}'.lstrip() + "\n" + \
        "\n".join(json.dumps(r) for r in records) + "\n"

    win, model, seq, usage = dsh_usage.parse_stream(text)
    ck("the window is the LAST request/context record", win, 2000)
    ck("the model is the LAST request/context record", model, "new:tag")
    ck("the usage is the LATEST usage object", usage.get("totalTokens"), 400)
    ck("the usage seq is reported", seq, 8)
    ck("cache-read tokens are carried", usage.get("cacheReadTokens"), 300)

    # A torn record is STATE, not skippable noise (swallowed-exception): the reader
    # surfaces it with the line number instead of silently dropping the line.
    bad = json.dumps(records[0])[:20] + "\n"
    try:
        dsh_usage.parse_stream(bad)
        ck("a malformed record is surfaced, not skipped", "allowed", "raised")
    except ValueError as e:
        ck("a malformed record is surfaced, not skipped",
           "raised" if "line 1" in str(e) else str(e)[:40], "raised")

    # A stream with usage but no context record must not invent a window.
    text2 = json.dumps(records[1]) + "\n"
    _, _, _, usage2 = dsh_usage.parse_stream(text2)
    win2, model2, seq2, _ = dsh_usage.parse_stream(text2)
    ck("no context record means no invented window", win2, None)
    ck("no context record means no invented model", model2, None)

    # Occupancy math: integers, percentage of the window, and a refuse to divide
    # by an unknown window rather than print a bogus percent.
    import math
    ck("occupancy is used/window as a percentage",
       dsh_usage.occupancy(400, 2000), 20.0)
    ck("an unknown window refuses a percentage", dsh_usage.occupancy(400, None), None)

    for x in fails:
        print("FAIL:", x)
    print("test_dsh_usage:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
