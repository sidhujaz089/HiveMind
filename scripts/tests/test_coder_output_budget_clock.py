#!/usr/bin/env python3
"""t1432: the FIRST attempt's output budget must fit the box's clock, not just the window.

Dispatch p674-t1258-session-review (2026-10-18, qwen3.8:27b-mtp-q4_K_M-56k) was handed
53,844 output tokens -- 57,344 window minus a 4,500-char prompt (est 1,500) minus the 2,000
margin -- and timed out at 10,724 s having emitted NOTHING. 53,844 / 10,724 = 5.02 tok/s, the
box's measured decode rate, consistent with settings.DECODE_FLOOR_TOK_S = 4.0.

The socket is not the cause: ask() DERIVES its timeout from the budget through
settings.timeout_for, whose static branch is int(tokens / DECODE_FLOOR_TOK_S * TIMEOUT_SAFETY_FACTOR + PROMPT_EVAL_SLACK_S)
with DECODE_FLOOR_TOK_S=4.0 (settings.py:677), TIMEOUT_SAFETY_FACTOR=2.0 (settings.py:689),
PROMPT_EVAL_SLACK_S=600 (settings.py:678). For 53844 tokens that is 27522 s -- longer than the
3-hour (10800 s) wall the dispatch died on. The budget was the binding limit, not the socket.

The budget is therefore a WALL-CLOCK commitment, and a budget whose derived timeout exceeds the
dispatch's own wall is a licence to burn the lane: the attempt can never be cut off at
done_reason="length", which is the ONLY output cut-off coder.py recovers from (Truncated ->
finish_truncated, answering in FINISH_TOKENS=4000). A cut-off is recoverable. A 10724-second
timeout is not, and the model spends all of it reasoning because nothing reserves room for the
answer (docs/skills/briefing-the-local-model, which forbids the reflex of doubling the budget).

This suite pins ONE new constant and the clamp that uses it, in projects/local-inference/coder.py.
It asserts the number's USE -- the time settings.py derives from it -- not the number itself, so
retuning the rate in settings.py moves this guard with it.
"""
import contextlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]           # scripts/tests -> repo root
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402  -- the pattern scripts/tests/test_coder_first_budget.py uses

# The dispatch's own wall. The recorded death was 10724 s; the knob that killed it is the 3-hour
# (10800 s) dispatch timeout. A budget that needs MORE than this to decode at the box's declared
# floor rate can never finish, so it must not be handed out.
DISPATCH_WALL_S = 10800

# Real windows this repository serves, from the files, not invented round numbers:
# 57344  = the 56k auditor tag of the failed dispatch (56 KiB), the case in the evidence
# 262144 = Swift's window, served_window()'s vLLM path
# 65536  = the coder profile's 64k window that t477 raised the budget for
# 32768  = the legacy 32k window
AUDITOR_WINDOW, SWIFT_WINDOW, LEGACY_WINDOW, MID_WINDOW = 57344, 262144, 32768, 65536


def _settings():
    """The module coder.py itself asks for the socket timeout (ask() -> settings.timeout_for)."""
    import settings
    return settings


def _budget_seconds(budget):
    """Seconds settings.py will allow a generation of `budget` tokens -- the SAME call ask()
    makes, at the declared floor rate, with no telemetry history (harness/model omitted, so
    settings.timeout_for takes its static branch and touches no file, server or model)."""
    s = _settings()
    return s.timeout_for(budget, floor_tok_s=s.DECODE_FLOOR_TOK_S)


def test_the_cap_exists_and_sits_above_the_floor():
    """Red on main: coder.py has no MAX_TOKENS_FIRST.

    The cap must not sit below the floor, or first_budget's max(min(room, CAP), FLOOR) would
    hand out MIN_TOKENS_FIRST -- a number above the cap -- and every clock assertion below
    would be false on every window."""
    assert hasattr(coder, "MAX_TOKENS_FIRST"), \
        "coder.py has no MAX_TOKENS_FIRST: the first budget is still every token the window holds"
    cap = coder.MAX_TOKENS_FIRST
    assert isinstance(cap, int) and cap > 0, f"MAX_TOKENS_FIRST must be a positive int, got {cap!r}"
    assert coder.MIN_TOKENS_FIRST <= cap, (
        f"the floor MIN_TOKENS_FIRST={coder.MIN_TOKENS_FIRST} exceeds the cap {cap}, so every "
        f"window hands out a budget above the cap and the bound is a comment, not a bound")


def test_a_first_budget_can_finish_inside_the_dispatch_wall():
    """THE DEFECT, AS ARITHMETIC. For each window the box serves, the budget coder hands out
    must decode inside the dispatch's own wall at the rate settings.py declares the box runs at.

    53844 -> 27522 s today. A budget that outlives the wall cannot be cut off at
    done_reason='length', so the truncation recovery (finish_truncated) is unreachable by
    construction -- which is why a larger window is a bigger liability, not more headroom."""
    for prompt_chars, window in [(4500, AUDITOR_WINDOW),      # the p674 case: 57344-1500-2000
                                 (12000, SWIFT_WINDOW),
                                 (40000, SWIFT_WINDOW),
                                 (3000, MID_WINDOW),
                                 (120000, MID_WINDOW),
                                 (4000, LEGACY_WINDOW)]:
        b = coder.first_budget(prompt_chars, window)
        assert isinstance(b, int), f"first_budget must return an int, got {b!r}"
        secs = _budget_seconds(b)
        assert secs <= DISPATCH_WALL_S, (
            f"prompt {prompt_chars}c, window {window}: first_budget returned {b} tokens, which "
            f"settings.timeout_for puts at {secs}s -- longer than a {DISPATCH_WALL_S}s dispatch. "
            f"That is the p674 shape: the budget outlives the wall, so the attempt is killed "
            f"mid-reasoning with no output and finish_truncated never runs.")


def test_the_observed_incident_is_no_longer_reproducible():
    """The exact pair from the note, asserted by name so a future raise of the cap has to
    argue with this test. 53844 tokens is 10724s at 5.02 tok/s -- the observed rate. No
    window's budget may need more than the wall the box allows it."""
    b = coder.first_budget(4500, AUDITOR_WINDOW)
    assert b <= coder.MAX_TOKENS_FIRST, (
        f"the 56k auditor window produced a first budget of {b}; the evidence's 53844 came from "
        f"this same arithmetic (57344 - 1500 - 2000) and burned 10724s for nothing")


def test_no_window_still_gets_the_floor():
    """NON-VACUITY. The cap must not starve the answer. main() builds its WINDOW-FULL refusal
    on MIN_TOKENS_FIRST, so a window with no room must still be handed the floor and be refused
    as WINDOW-FULL by the loop, not started with a token-granting budget."""
    assert coder.first_budget(100000, 0) >= coder.MIN_TOKENS_FIRST
    assert coder.first_budget(100000, None) >= coder.MIN_TOKENS_FIRST
    assert coder.first_budget(60000, LEGACY_WINDOW) >= coder.MIN_TOKENS_FIRST


def test_budget_always_fits_beside_its_prompt():
    """first_budget and prompt_fits are the two halves of ONE decision (prompt_fits is called
    on this same pair by main(), and is named in first_budget's own docstring). A budget
    prompt_fits refuses is a budget the loop should never be handed. Checked by calling the
    module's own pair -- not by restating the arithmetic in this file."""
    for prompt_chars, window in [(4500, AUDITOR_WINDOW), (12000, SWIFT_WINDOW),
                                 (40000, SWIFT_WINDOW), (4000, LEGACY_WINDOW)]:
        b = coder.first_budget(prompt_chars, window)
        fits, est = coder.prompt_fits(prompt_chars, b, window)
        assert fits, (f"prompt {prompt_chars}c, window {window}: first_budget handed the loop "
                     f"{b} tokens (prompt est {est}) which prompt_fits refuses at that window")


def test_the_cap_announces_itself():
    """A bound the reader cannot see is the defect never-truncate-a-record exists to stop.
    When the clamp bites, first_budget says so; when it does not, it stays quiet."""
    loud = io.StringIO()
    with contextlib.redirect_stdout(loud), contextlib.redirect_stderr(loud):
        b = coder.first_budget(12000, SWIFT_WINDOW)   # 262144 - 4000 - 2000 >> any cap
    out = loud.getvalue()
    assert str(b) in out, (f"the first budget was clamped to {b} and nothing was printed; the "
                           f"next reader sees a number and no reason for it")
    assert "MAX_TOKENS_FIRST" in out, (
        f"the clamp fired and the announcement does not name MAX_TOKENS_FIRST, so the reader "
        f"cannot find the constant that bound it: {out!r}")

    quiet = io.StringIO()
    with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
        coder.first_budget(40000, LEGACY_WINDOW)      # room 17435: below any cap, never bites
    assert "MAX_TOKENS_FIRST" not in quiet.getvalue(), \
        "the announcement fired on a window that never needed capping"


if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as e:            # noqa: BLE001 -- report, do not swallow
                fails.append(name)
                print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fails)} failed" if fails else "\nall green")
    sys.exit(1 if fails else 0)
