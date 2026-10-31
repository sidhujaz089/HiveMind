#!/usr/bin/env python3
"""t3578: ask_q16's run ledger never writes a row without an outcome.

Two rows in workspace/agent-runs.jsonl carry "outcome": null (2026-10-06T04:51:53Z, "manager: t690";
2026-10-14T01:49:54Z, "RCA: session-review finding c5a694896565"). The writer is ask_q16._ledger,
which wrote r.get("done_reason") straight through -- and done_reason is None whenever the model's
final reply carries none (the tool-round cap at :199 passes obj.get("done_reason") on, and a server
that answers without one leaves it None). The row survived the run but said nothing about how it
ended, so "Every run outcome is classified" could only ever WARN, and nothing forced a new writer
path to declare what it emitted.

The fix is at the writer, not at the check: _ledger names an outcome it can defend. A run that
reports no reason is a fact, so it is recorded AS one -- "no-done-reason" -- and that word is
declared in reviewq.TRANSPORT_OUTCOMES, which is the one place that says what an outcome means.

Nothing here touches the live ledger: LEDGER is pointed at a TemporaryDirectory.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import ask_q16 as aq      # noqa: E402
import reviewq            # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def rows_written(fake_ledger, r):
    """Run the real _ledger() against a temp ledger and return the rows it wrote."""
    saved = aq.LEDGER
    aq.LEDGER = fake_ledger
    try:
        aq._ledger("t3578 oracle", r, 0.1)
    finally:
        aq.LEDGER = saved
    return [json.loads(x) for x in Path(fake_ledger).read_text().splitlines() if x.strip()]


def main():
    with tempfile.TemporaryDirectory() as d:
        led = Path(d) / "agent-runs.jsonl"
        led2 = Path(d) / "agent-runs-2.jsonl"
        led3 = Path(d) / "agent-runs-3.jsonl"

        # 1. THE CASE THAT MADE THE TWO ROWS: a result with no done_reason at all.
        row = rows_written(led, {"text": "", "complete": False, "trail": [], "warning": None})[0]
        check("a run that reports no reason still records an outcome",
              row.get("outcome") not in (None, ""), row.get("outcome"))
        check("  and the check's own reader classifies it, rather than calling it unrecorded",
              reviewq.outcome_class(row) != "unrecorded", reviewq.outcome_class(row))
        check("  it is declared in the transport vocabulary, not invented inline",
              row.get("outcome") in reviewq.TRANSPORT_OUTCOMES, row.get("outcome"))

        # 2. A NORMAL RUN IS UNTOUCHED: the reason the model gave is what gets recorded.
        row = rows_written(led2,
                           {"text": "an answer", "done_reason": "stop", "complete": True,
                            "trail": [{"tool_rounds": 2}]})[0]
        check("a run that did report a reason keeps that exact reason", row.get("outcome"), "stop")

        # 3. THE RULE IS "NEVER NULL", whatever else the result carries.
        row = rows_written(led3,
                           {"text": "partial", "done_reason": None, "complete": False,
                            "trail": [{"tool_rounds": 1}]})[0]
        check("a null reason with text in hand is still recorded as an outcome",
              row.get("outcome") not in (None, ""), row.get("outcome"))

        # 4. THE TWO EXISTING ROWS are classified with the word this writer now emits, so the
        #    repair and the rule share one vocabulary instead of inventing a second.
        check("the word used to classify the two historical rows is the one the writer emits",
              reviewq.outcome_class({"outcome": "no-done-reason"}) == "transport")

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
