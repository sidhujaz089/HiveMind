#!/usr/bin/env python3
"""t950 -- the promise ledger (RCA 05248445e09b D-1).

Red on main for ONE reason: scripts/session_review.py has no record_promise_ledger, so no endpoint
records that a stated intention was discharged by a later action. The RCA's own pair of sentences
is fixture A: the assistant said "I'll start by listing the whole open backlog." (turn 216 of 294)
and listed the whole backlog 1.33 seconds later -- and no field in the finding can say so.

Run: python3 scripts/tests/test_promise_ledger.py   (pytest is not installed)
"""
import ast
import inspect
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import session_review as sr  # noqa: E402

PROMISE = "I'll start by listing the whole open backlog."
DISCHARGE = "Listed the whole open backlog: 209 tasks (t286) -- committed df1bcd06 in the queue."
UNRELATED = "The GPU is under a CUDA 12.8 build and the harness is fine."
LETME = "Let me check the open backlog and report back."
NO_PROMISE = "The queue is 209 items long today."
SECOND = "I will rebuild the index and report the new count."
FIELDS = {"session", "turn", "quote", "promise_phrase", "matching_actions_within_n_turns",
          "artefact", "verdict", "evidence_channel", "at"}
VERDICTS = {"executed-in-text", "undischarged"}
Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _writer():
    f = getattr(sr, "record_promise_ledger", None)
    assert callable(f), (
        "scripts/session_review.py defines no record_promise_ledger, so no endpoint records that a "
        "stated intention was discharged by a later action (t950 / RCA 05248445e09b D-1).")
    return f


def _rows(path):
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _call_site():
    """(src, [(enclosing-function-name, ast.Call)]) for every call to the writer that sits inside a
    function which also calls scripted_items -- the extraction endpoint the RCA names as the append
    point, because it is the one place that has the transcript and the request id in hand together."""
    src = inspect.getsource(sr)
    tree = ast.parse(src)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        calls_items = any(isinstance(c, ast.Call) and getattr(c.func, "id", "") == "scripted_items"
                          for c in ast.walk(node))
        if not calls_items:
            continue
        for c in ast.walk(node):
            if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "record_promise_ledger":
                found.append((node.name, c))
                break
    return src, found


def test_a_discharged_promise_is_recorded_as_executed_in_text():
    """The RCA's own pair: the promise, and the later text that discharged it."""
    write = _writer()
    ts = [("assistant", PROMISE + " " + NO_PROMISE),
          ("user", "ok"),
          ("assistant", DISCHARGE),
          ("assistant", UNRELATED + " t500 is the other item."),
          ("user", "thanks")]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "promise-ledger.jsonl"
        rows = write(ts, "sess-alpha", out_path=out)
        got = _rows(out)
    assert rows == got, "the writer must return the rows it appended: %r vs %r" % (rows, got)
    assert len(got) == 1, (
        "one promise sentence in this transcript (the second sentence of the first turn carries no "
        "promise phrasing), got %d rows: %r" % (len(got), got))
    r = got[0]
    assert r["quote"] == PROMISE, "the quote is the sentence exactly as sentences() returned it: %r" % (r["quote"],)
    assert r["turn"] == 0, "the promise sits at ts[0], got turn=%r" % (r["turn"],)
    assert r["session"] == "sess-alpha", "the session id comes from the caller, got %r" % (r["session"],)
    assert r["promise_phrase"] == "I'll", "promise_phrase is the matched text verbatim, got %r" % (r["promise_phrase"],)
    assert r["verdict"] in VERDICTS, "verdict %r is not one of the two words this ledger speaks" % (r["verdict"],)
    assert r["verdict"] == "executed-in-text", (
        "a later assistant turn repeats this promise's subject ('whole open backlog'), so a row "
        "reading %r records a kept promise as a dropped commitment" % (r["verdict"],))
    assert r["matching_actions_within_n_turns"] == 1, (
        "exactly one of the following assistant turns repeats the promise's subject, got %r"
        % (r["matching_actions_within_n_turns"],))
    assert set(r["artefact"]) == {"t286", "df1bcd06"}, (
        "the artefacts that discharged this promise are the task id and the commit hash in the "
        "MATCHING turn, got %r" % (r["artefact"],))
    assert "t500" not in r["artefact"], (
        "t500 sits in a NON-matching turn; a ledger that collects ids from every following turn "
        "attributes unrelated work to this promise, got %r" % (r["artefact"],))
    assert isinstance(r["artefact"], list) and all(isinstance(x, str) for x in r["artefact"]), (
        "artefact is a list of ids, got %r" % (r["artefact"],))
    assert r["evidence_channel"] == "assistant-text", (
        "this endpoint sees assistant text only -- turns() drops tool traffic -- got %r"
        % (r["evidence_channel"],))
    assert set(r) == FIELDS, "row fields are the contract: expected %s, got %s" % (sorted(FIELDS), sorted(r))
    assert Z.match(str(r["at"])), "at=%r is not a Z timestamp" % (r["at"],)


def test_a_promise_with_nothing_after_it_is_undischarged():
    """The ledger's reason for existing: a promise nobody came back to, recorded as such."""
    write = _writer()
    ts = [("user", "how is the queue today?"),
          ("assistant", "Counting now, give me a moment."),
          ("assistant", LETME),
          ("assistant", UNRELATED),
          ("user", "thanks")]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "promise-ledger.jsonl"
        rows = write(ts, "sess-beta", out_path=out)
        got = _rows(out)
    assert rows == got, "the writer must return the rows it appended: %r vs %r" % (rows, got)
    assert len(got) == 1, "one promise sentence, got %d rows: %r" % (len(got), got)
    r = got[0]
    assert r["turn"] == 2, "the promise sits at ts[2], got turn=%r" % (r["turn"],)
    assert r["session"] == "sess-beta", "got %r" % (r["session"],)
    assert r["promise_phrase"] == "Let me", (
        "the RCA's trigger list is 'I'll / let me / next I'll'; got %r" % (r["promise_phrase"],))
    assert r["verdict"] == "undischarged", (
        "nothing after it repeats its subject, so it is the one row type the queue needs, got %r"
        % (r["verdict"],))
    assert r["matching_actions_within_n_turns"] == 0, "got %r" % (r["matching_actions_within_n_turns"],)
    assert r["artefact"] == [], "an undischarged promise has no artefact, got %r" % (r["artefact"],)


def test_a_sentence_with_no_promise_phrasing_gets_no_row():
    """The ledger is not a transcript log: a sentence with no commitment writes nothing at all."""
    write = _writer()
    ts = [("assistant", NO_PROMISE),
          ("assistant", "The harness is fine and the queue is 209 items long.")]
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "promise-ledger.jsonl"
        rows = write(ts, "sess-gamma", out_path=out)
        assert rows == [], "neither sentence carries a promise phrasing, got %r" % (rows,)
        assert not out.exists() or not out.read_text(encoding="utf-8").strip(), (
            "a session with no promise must append nothing at all")


def test_the_ledger_appends_one_row_per_session_in_call_order():
    """Append-only, one row per promise, and each row carries its own session's sentence."""
    write = _writer()
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "promise-ledger.jsonl"
        write([("assistant", PROMISE)], "sess-one", out_path=out)
        write([("assistant", SECOND)], "sess-two", out_path=out)
        got = _rows(out)
    assert [r["session"] for r in got] == ["sess-one", "sess-two"], (
        "append-only, in call order, one row per session: %r" % ([r["session"] for r in got],))
    assert [r["quote"] for r in got] == [PROMISE, SECOND], (
        "each row records its own session's sentence verbatim: %r" % ([r["quote"] for r in got],))
    assert [r["promise_phrase"] for r in got] == ["I'll", "I will"], (
        "both phrasings on the RCA's list are recorded, by their own matched text: %r"
        % ([r["promise_phrase"] for r in got],))
    assert [r["verdict"] for r in got] == ["undischarged", "undischarged"], (
        "nothing follows either promise: %r" % ([r["verdict"] for r in got],))


def test_the_promise_ledger_is_its_own_file_and_never_the_review_log():
    """workspace/session-reviews/ledger.jsonl says a review step ran. This says a promise was kept.
    Its readers must keep parsing what they parse today, so this is a new file, not that one."""
    _writer()
    assert hasattr(sr, "PROMISE_LEDGER"), "no PROMISE_LEDGER constant: the ledger's path is undeclared"
    target = str(sr.PROMISE_LEDGER)
    assert target.endswith("workspace/session-reviews/promise-ledger.jsonl"), "PROMISE_LEDGER=%r" % (target,)
    assert target != str(sr.LEDGER), "PROMISE_LEDGER must not be the review log: %r" % (target,)


def test_the_extraction_endpoint_calls_the_writer_and_passes_the_session_through():
    """A writer no endpoint calls leaves the ledger empty while this suite is green. The RCA names
    the extraction endpoint -- the function that calls scripted_items -- as the append point, and a
    row with no session id cannot be attributed to a session, so both are pinned by the AST."""
    _writer()
    src, found = _call_site()
    assert found, (
        "no function in scripts/session_review.py that calls scripted_items also calls "
        "record_promise_ledger: the ledger stays empty in production while this suite passes "
        "(RCA 05248445e09b D-1 names scripted_items as the append point)")
    name, call = found[0]
    shown = ast.get_source_segment(src, call)
    assert len(call.args) + len(call.keywords) >= 2, (
        "the call %s in %s passes no session id: a ledger row that cannot say which session made "
        "the promise is not auditable" % (shown, name))
    passed = ""
    if len(call.args) >= 2:
        passed = ast.get_source_segment(src, call.args[1]) or ""
    for kw in call.keywords:
        if kw.arg == "session":
            passed = (passed + " " + (ast.get_source_segment(src, kw.value) or "")).strip()
    assert passed, "the call %s passes no session argument" % (shown,)
    assert "id" in passed, (
        "the session argument %r is not an id taken from the request the endpoint is handling: "
        "the RCA's row leads with session" % (passed,))


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    assert len(tests) >= 6, "the suite defines %d tests; expected at least 6" % (len(tests),)
    failed = 0
    for t in tests:
        try:
            t()
            print("ok   %s" % (t.__name__,))
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (t.__name__, e))
        except Exception as e:
            failed += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
    print("%d test(s), %d failed" % (len(tests), failed))
    sys.exit(1 if failed else 0)
