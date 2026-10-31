#!/usr/bin/env python3
"""t1278 -- a socket timeout on a coder.py model call must be a recorded, resumable outcome.

The dispatch that filed this task burned 7485s reasoning and was killed by the socket:
"ERROR TimeoutError: timed out at 7485s". coder.ask() calls infer.chat() bare, so the
timeout escapes as a raw exception, ask.last keeps no done_reason, and the Truncated ->
finish_truncated resume (the skill's "come back and answer" fix) never runs.

RED ON MAIN: coder.chat_with_timeout does not exist on main, so nothing below it can pass.

No live state: no model call, no server, no file written. infer.chat is faked to raise
immediately and coder._tel is faked, so the timeout's telemetry row is captured in a list
instead of being appended to the live run-telemetry log.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
for _p in (REPO / "scripts", REPO / "projects" / "local-inference"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import coder
import infer

RESULTS = []


def report(label, ok, detail=""):
    RESULTS.append(bool(ok))
    print(("  ok  " if ok else "  FAIL") + " | " + label + ((" -> " + detail) if detail else ""))


def _boom(body, timeout=None):
    raise TimeoutError("timed out at %ss" % timeout)


def _ok(body, timeout=None):
    return {"message": {"content": "  hi  ", "thinking": "was reasoning"},
            "done_reason": "stop", "eval_count": 12}


def main():
    print("t1278: a socket timeout on a model call is a legible done_reason='timeout'")
    helper = getattr(coder, "chat_with_timeout", None)
    report("coder.chat_with_timeout exists (the missing change)", callable(helper))
    if not callable(helper):
        return 1

    body = {"model": "m", "options": {"num_predict": 55295}}
    timeout_s = 7485

    rows = []
    real_tel = coder._tel
    coder._tel = lambda *a, **k: rows.append(a)
    real_chat = infer.chat
    try:
        infer.chat = _boom
        coder.ask.last = {}
        n_reason = len(coder.ask.reasoning)
        raised = None
        try:
            helper(body, timeout_s, True)
        except coder.Truncated:
            raised = "Truncated"
        except Exception as exc:
            raised = type(exc).__name__
        report("a timeout raises the Truncated the loop already resumes from",
               raised == "Truncated", "raised=%r" % raised)
        report("ask.last records the outcome as done_reason='timeout'",
               coder.ask.last.get("done_reason") == "timeout", repr(coder.ask.last))
        report("the timed-out call is recorded with an empty answer, not a fabricated one",
               (coder.ask.last.get("content"), coder.ask.last.get("thinking"),
                coder.ask.last.get("eval_count")) == ("", "", 0), repr(coder.ask.last))
        report("ask.reasoning grows by the one empty turn it took",
               len(coder.ask.reasoning) == n_reason + 1, str(len(coder.ask.reasoning)))
        report("the timeout is telemetryed exactly once", len(rows) == 1, repr(rows))
        row = tuple(rows[0]) if rows else ()
        report("the telemetry row names the harness",
               len(row) > 0 and row[0] == "coder.py/native", repr(row[:1]))
        report("the telemetry row names the model", len(row) > 1 and row[1] == "m", repr(row[1:2]))
        report("the telemetry row carries the real output budget, not None",
               len(row) > 3 and row[3] == 55295, repr(row[3:4]))
        report("the telemetry row's done_reason is 'timeout', not 'length'",
               len(row) > 6 and row[6] == "timeout", repr(row[6:7]))
        report("the telemetry row records the cap that fired",
               len(row) > 7 and row[7] == timeout_s, repr(row[7:8]))

        coder.ask.last = {}
        del rows[:]
        raised = None
        try:
            helper(body, timeout_s, False)
        except coder.Truncated:
            raised = "Truncated"
        except TimeoutError:
            raised = "TimeoutError"
        except Exception as exc:
            raised = type(exc).__name__
        report("with raise_on_length False the raw timeout still propagates unchanged",
               raised == "TimeoutError", "raised=%r" % raised)
        report("...and nothing is recorded for a caller that did not opt in",
               (not rows) and coder.ask.last == {}, repr((rows, coder.ask.last)))
    finally:
        infer.chat = real_chat
        coder._tel = real_tel

    sent = _ok(body, timeout_s)
    infer.chat = lambda b, timeout=None: sent
    coder._tel = lambda *a, **k: rows.append(a)
    try:
        del rows[:]
        coder.ask.last = {}
        got = helper(body, timeout_s, True)
        report("the success path hands infer.chat's body straight back, unchanged",
               got is sent, "returned %r" % (type(got).__name__,))
        report("the success path records no timeout row", rows == [], repr(rows))
        report("the success path leaves ask.last to ask(), not to the wrapper",
               coder.ask.last == {}, repr(coder.ask.last))
    finally:
        infer.chat = real_chat
        coder._tel = real_tel

    return 0 if (RESULTS and all(RESULTS)) else 1


if __name__ == "__main__":
    sys.exit(main())
