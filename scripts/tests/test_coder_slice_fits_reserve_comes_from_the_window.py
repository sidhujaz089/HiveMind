#!/usr/bin/env python3
"""t980: coder.py's file gate charges FILE_REPLY_RESERVE to the REPLY BUDGET, which
first_budget() has already spent -- so a file the context window can hold is refused.

Dispatch p314-t800-flow-coverage on swift (workspace/tasks/t980--...md, notes 2026-10-08):
"output budget 253612 tokens (window 262144)" -- 96.7% of the window to the answer -- and the
model was refused the file it had to edit, five times across attempts 2, 4 and 5:
"scripts/tests/test_hook_flow_link.py: no room for more of it beside the prompt. Work from
what you already have". It cannot write code against an oracle it is forbidden to read, while
the brief promises "the whole file is in your window".

RED ON MAIN, for the missing change: slice_fits asks prompt_fits for min(budget,
FILE_REPLY_RESERVE) tokens of reply beside the GROWN prompt, and budget is first_budget's --
every token the window held beside the STARTING prompt, shrinking every time a file is served.
Once the grown prompt passes ~228,000 tokens the 32,000-token demand can never be met again,
so the file slot stays closed for the rest of the run. GREEN when the reserve is a claim on the
WINDOW: a file is served while it leaves a positive reply room beside the grown prompt.

The numbers are coder.py's own arithmetic (prompt_fits' 3.0 chars/token, margin 2000):
first_budget(700000, 262144) = 262144 - int(700000/3) - 2000 = 26,811.
No subprocess, no network, no model, nothing written: safe to run unattended.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]     # scripts/tests -> repo root
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402  -- the stanza scripts/tests/test_coder_first_budget.py uses

WINDOW = 262144           # the window named in the note
GROWN = 700_000           # chars; int(700000/3.0) = 233,333 tokens of prompt
MARGIN = 2000             # prompt_fits' own default margin, also first_budget's

# The refused artefact, as a REAL file -- the task's own dossier. Not a size I imagined:
# every assertion below takes its slice size from len(SLICE) and checks the arithmetic.
ARTEFACT = (ROOT / "workspace" / "tasks" /
            "t980--harness-coder-py-output-budget-consumes-96-7-of.md")
SLICE = ARTEFACT.read_text(encoding="utf-8")


def _est(chars):
    """What coder.py itself charges for `chars` (prompt_fits' pessimistic 3.0 chars/token)."""
    _fits, est = coder.prompt_fits(chars, 0, WINDOW)
    return est


def test_the_fixture_still_describes_the_defect():
    """The fixture's decay test: if the numbers stop exercising the defect, say so.

    If FILE_REPLY_RESERVE, first_budget's formula or WINDOW ever change, this suite's red
    reason must not silently become "nothing to refuse" -- the premise is asserted here.
    """
    assert len(SLICE) >= 1000, f"{ARTEFACT} is {len(SLICE)} chars; the slice must be a real file"
    est = _est(GROWN + len(SLICE))
    spent = coder.first_budget(GROWN, WINDOW)
    assert spent < coder.FILE_REPLY_RESERVE, (
        f"first_budget({GROWN}, {WINDOW}) = {spent}, not below the "
        f"{coder.FILE_REPLY_RESERVE} reserve: the pair would not exercise the defect")
    assert est + min(spent, coder.FILE_REPLY_RESERVE) + MARGIN > WINDOW, (
        f"est {est} + reserve {min(spent, coder.FILE_REPLY_RESERVE)} + {MARGIN} fits "
        f"{WINDOW}: today's gate would not refuse, so this fixture tests nothing")
    assert est + MARGIN < WINDOW, f"est {est} alone fills the {WINDOW} window: nothing to give"


def test_a_file_the_window_can_hold_is_not_refused():
    """The behaviour the brief promises: the model can read the file it must edit.

    The budget is the one first_budget hands out for the grown prompt (26,811 tokens). The
    slice costs ~1,500 tokens of prompt and leaves the window ~25,000 tokens of reply room;
    today it is refused because 32,000 tokens of reply no longer fit -- a claim the window
    never made.
    """
    budget = coder.first_budget(GROWN, WINDOW)
    fits, est, new_budget = coder.slice_fits(GROWN, len(SLICE), budget, WINDOW)
    assert fits, (
        f"slice_fits refused a {len(SLICE)}-byte file: ~{est} tokens of prompt + the "
        f"{coder.FILE_REPLY_RESERVE}-token reserve + {MARGIN} margin = "
        f"{est + min(budget, coder.FILE_REPLY_RESERVE) + MARGIN} tokens, over the "
        f"{WINDOW}-token window, while the window has {WINDOW - est - MARGIN} tokens left "
        "beside the prompt. The reserve is charged to the reply budget the run has already "
        "spent (t980).")
    assert new_budget > 0, f"the file was served and left {new_budget} tokens of reply"
    assert new_budget == max(0, min(budget, coder.first_budget(GROWN + len(SLICE), WINDOW))), (
        f"serving {len(SLICE)} chars returned {new_budget}, not the room left beside the "
        "grown prompt: a served slice must pay for what it was shown")


def test_a_grown_prompt_that_fills_the_window_is_still_refused():
    """NEGATIVE: moving the reserve to the window must not become 'serve anything'."""
    filled = 3 * (WINDOW - MARGIN + 1)          # est = 260,145 tokens: no room for an answer
    assert _est(filled) + MARGIN > WINDOW, "fixture premise: this prompt fills the window"
    budget = coder.first_budget(filled, WINDOW)
    fits, est, new_budget = coder.slice_fits(filled, len(SLICE), budget, WINDOW)
    assert fits is False, (
        f"slice_fits served a file into a {WINDOW}-token window that already holds {est} "
        "tokens of prompt: the gate must refuse")
    assert new_budget == budget, (
        f"on refusal the reply budget moved from {budget} to {new_budget}: a refusal that "
        "spends the budget leaves the next attempt nothing to answer with")


def test_a_small_prompt_is_refused_no_more_than_today():
    """NEGATIVE: the change must widen the gate nowhere it already says yes."""
    budget = coder.first_budget(30_000, WINDOW)          # a 10,000-token prompt
    fits, est, new_budget = coder.slice_fits(30_000, len(SLICE), budget, WINDOW)
    assert fits, "a small prompt leaves the whole window; refusing it is wrong"
    assert est == _est(30_000 + len(SLICE)), f"est {est} is not prompt_fits' estimate"
    assert new_budget == max(0, min(budget, coder.first_budget(30_000 + len(SLICE), WINDOW))), (
        f"{new_budget} is not first_budget of the grown prompt: the reply budget must not "
        "be inflated by the reserve")


def test_an_unknown_window_shrinks_nothing():
    """An unknown window is not evidence -- slice_fits' first line, unchanged."""
    fits, est, new_budget = coder.slice_fits(GROWN, len(SLICE), 5000, 0)
    assert fits, "an unknown window must not refuse a file"
    assert new_budget == 5000, f"the budget was changed to {new_budget} on an unknown window"
    assert est == int((GROWN + len(SLICE)) / 3.0), f"est {est} is not the 3.0 chars/token estimate"


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok   {name}")
        except Exception as exc:  # noqa: BLE001 -- a raised test is a failed test
            failed += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
