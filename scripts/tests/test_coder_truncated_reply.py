#!/usr/bin/env python3
"""t1003 -- coder.py must never accept a model reply that ended at the token cap.

EVIDENCE: dispatch p339-t760-flow-change on swift, 2026-10-09T17:35:27Z, attempt 2 logged
'asked for 1 file(s), gave 1 new (232 tok, 12s)' -- 232 tokens cannot hold flow_change.py
(114+ lines). The model ran with reasoning=on, spent its generation budget thinking, and the
harness accepted the fragment as file content; the pre-existing suite broke in attempts 1 and
3. No done_reason=length check fired, so no re-ask with reasoning off happened and the stub
propagated.

RED ON MAIN, FOR THIS REASON: coder.ask() returns a reply whose done_reason is \"length\"
whenever the caller leaves raise_on_length at its default. The file-serving branch is such a
caller, and so is the salvage helper -- finish_truncated calls ask_fn with think=False,
max_tokens=FINISH_TOKENS and no raise_on_length -- so both accept a reasoning-starved
fragment as a complete answer. Two tests are red today for exactly that:
test_a_length_cut_reply_is_not_returned_as_a_complete_answer and
test_a_cut_off_salvage_is_reported_not_returned_as_the_answer. Everything else in the file
passes on main, so a red run points at the two sites, not at the oracle.

No model, no server, no subprocess, nothing written to disk: both transports ask() can reach
are faked (the native infer.chat and the gateway's urllib), and _tel is patched out, so
running this cannot dirty the worktree.

Run: python3 scripts/tests/test_coder_truncated_reply.py
"""
import json
import pathlib
import sys
import types
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))

import coder

FAILS = []

FRAGMENT = "def load(path):\n    data = json.loads(path.read_text("   # cut mid-statement
WHOLE = "def load(path):\n    return json.loads(path.read_text())\n"
FULL_ANSWER = '{"files": [{"path": "flow_change.py", "content": "..."}]}'

REPLY = [{}]      # the one scripted reply the fake transport hands back


def payload(content, done_reason, eval_count, prompt_eval):
    """ONE dict in both shapes, so no assertion depends on which branch ask() takes."""
    return {"message": {"content": content, "thinking": "reasoning so far"},
            "choices": [{"message": {"content": content}, "finish_reason": done_reason}],
            "usage": {"completion_tokens": eval_count, "prompt_tokens": prompt_eval},
            "done_reason": done_reason, "eval_count": eval_count,
            "prompt_eval_count": prompt_eval}


def check(what, got, want):
    ok = got == want
    print(("  ok  " if ok else "  FAIL") + f"  {what}: got {got!r} want {want!r}")
    if not ok:
        FAILS.append(what)


def install_transport():
    """Fake every transport ask() can reach, plus its telemetry.

    ask() does `import infer; d = infer.chat(body, timeout=timeout)` inside the function, so
    a stub in sys.modules is what that import binds -- no real module is imported. And ask()
    falls through to urllib.request.urlopen when the gateway is configured. Patching both
    means no socket is opened on either branch."""
    stub = types.ModuleType("infer")
    stub.chat = lambda body, timeout=None: REPLY[0]
    sys.modules["infer"] = stub

    class Resp:
        def __init__(self, body): self._p = json.dumps(body)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self._p

    urllib.request.urlopen = lambda req, timeout=None, **kw: Resp(REPLY[0])
    coder._tel = lambda *a, **k: None
    coder._gateway_sampling = lambda model: {}


def test_a_length_cut_reply_is_not_returned_as_a_complete_answer():
    install_transport()
    REPLY[0] = payload(FRAGMENT, "length", 232, 8000)
    outcome, returned = "returned", None
    try:
        returned = coder.ask("BRIEF TEXT", "swift", think=True, max_tokens=1000)[0]
    except coder.Truncated:
        outcome = "raised"
    check("ask() with raise_on_length left at its default does not hand back a reply cut "
          "at the token cap", outcome, "raised")
    check("ask.last records why the model stopped", coder.ask.last.get("done_reason"), "length")
    check("ask.last records what the model managed to say", coder.ask.last.get("content"), FRAGMENT)
    check("ask.last records the tokens it spent", coder.ask.last.get("eval_count"), 232)
    check("the truncated fragment is never the returned content", FRAGMENT in (returned or ""), False)


def test_the_explicit_flag_still_raises():
    install_transport()
    REPLY[0] = payload(FRAGMENT, "length", 232, 8000)
    outcome = "returned"
    try:
        coder.ask("BRIEF TEXT", "swift", think=True, max_tokens=1000, raise_on_length=True)
    except coder.Truncated:
        outcome = "raised"
    check("ask(..., raise_on_length=True) still raises", outcome, "raised")


def test_a_complete_reply_is_returned_whole():
    install_transport()
    REPLY[0] = payload(FULL_ANSWER, "stop", 900, 8000)
    out = coder.ask("BRIEF TEXT", "swift", think=True, max_tokens=1000)
    check("a reply that ended done_reason=stop comes back byte-identical", out[0], FULL_ANSWER)
    check("and keeps its token count", out[1], 900)
    check("and a complete reply is not turned into a truncation",
          coder.ask.last.get("done_reason"), "stop")


def test_the_window_tier_is_unchanged():
    check("prompt+output reaching the window is a window, not an output cut",
          coder.window_full(64000, 1536, 65536), True)
    check("a reply with room left is an output cut", coder.window_full(8000, 232, 65536), False)
    check("an unknown window is not evidence", coder.window_full(8000, 232, None), False)


def test_a_cut_off_salvage_is_reported_not_returned_as_the_answer():
    seen = []

    def fake_ask(prompt, model, **kw):
        # mirrors the real ask(): a call that is told a token-cap stop is a truncation raises
        seen.append(kw)
        if kw.get("raise_on_length"):
            raise coder.Truncated(232, 1.0, {"done_reason": "length", "content": FRAGMENT})
        return (FRAGMENT, 232, 1.0)

    raw, tok, secs, note = coder.finish_truncated(
        "CONVO", "swift", {"thinking": "reasoning so far"}, ask_fn=fake_ask, num_ctx=32768)
    check("the salvage asks for the answer with reasoning OFF", seen[0].get("think"), False)
    check("the salvage asks at the declared finish budget", seen[0].get("max_tokens"), coder.FINISH_TOKENS)
    check("the salvage call is told a token-cap stop is a truncation",
          seen[0].get("raise_on_length"), True)
    check("a cut-off salvage is not returned as the answer", raw, "")
    check("and the truncation is reported, not swallowed", "truncated" in note.lower(), True)
    check("and the note does not claim the salvage finished", "resumed from all" in note, False)
    check("the tokens the cut-off salvage spent reach the caller", tok, 232)
    check("and the seconds it spent", secs, 1.0)


def test_a_complete_salvage_is_still_returned_whole():
    def fake_ask(prompt, model, **kw):
        return (WHOLE, 400, 2.0)

    raw, tok, secs, note = coder.finish_truncated(
        "CONVO", "swift", {"thinking": "reasoning so far"}, ask_fn=fake_ask, num_ctx=32768)
    check("a salvage that finished is returned whole", raw, WHOLE)
    check("and its note says how much reasoning it resumed from", "chars" in note, True)
    check("and its token count is the salvage's", tok, 400)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"--- {name}")
            fn()
    print(f"\n{len(FAILS)} failed" + (": " + "; ".join(FAILS) if FAILS else ""))
    sys.exit(1 if FAILS else 0)
