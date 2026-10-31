#!/usr/bin/env python3
'''t1004 oracle: the sandbox must serve a requested file's NEXT slice when a
FILE_REPLY_RESERVE-token reply still fits beside the prompt, and must shrink the reply
budget to the room that is left.

p296-t690-backup-corpora (2026-10-09, swift): the model asked four times for its target
suite, scripts/tests/test_backup_prune_bundles.py (7,045 bytes on disk), and was refused
every time with "no room for more of it beside the prompt. Work from what you already
have". That refusal is arithmetic, not a shortage of room.

The branch that serves a file's next slice (projects/local-inference/coder.py, the
"if rel in opened:" branch of the need-loop, guard at line 2847) checks the slice against
the WHOLE reply budget. budget starts as first_budget(len(base)) (line 2718): every token
the window holds beside the STARTING prompt. first_budget subtracts prompt/3.0 and
prompt_fits adds it back at the same 3.0 chars/token, so prompt + budget + margin equals
the window EXACTLY -- adding any slice of non-zero size overflows it. A file the model has
already opened therefore cannot be extended at all, at any size, in any window. The
new-file branch was given the fix on 2026-10-08 ("a 7k-token file too large for a 262k
window"); the slice branch kept the old check, and its serve path never lowers the budget
after adding bytes, so the reply is charged for none of the prompt's growth.

Hermetic: reads the REAL target suite from the repository, calls the module's own
first_budget/prompt_fits, no model, no server, no subprocess, no writes.
'''
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

FAILS = []

WINDOW = 262144      # the window named in coder.py's own 2026-10-08 worked example
BASE = 30000         # a starting prompt, in characters
TARGET = ROOT / "scripts" / "tests" / "test_backup_prune_bundles.py"   # p296's target suite


def ck(name, cond, detail=""):
    print(f"  [{'ok' if cond else 'BAD'}]  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILS.append(name)


def suite_chars():
    """The real artefact, measured -- not a size I imagined (oracle-weak class)."""
    return len(TARGET.read_text(encoding="utf-8"))


def test_the_refusal_is_reproducible_with_the_module_s_own_arithmetic():
    """What coder.py does today, stated as a fact about today: a 7 KB file is refused by a
    262k window. Nothing here depends on my arithmetic -- both numbers come from coder.py."""
    n = suite_chars()
    ck("the real target suite is in the repository and small", 0 < n < 20000, f"{n} chars")
    budget = coder.first_budget(BASE, WINDOW)
    ck("first_budget hands the reply every token the window has beside the prompt",
       budget == WINDOW - int(BASE / 3.0) - 2000, f"budget={budget}")
    old_fits, old_est = coder.prompt_fits(BASE + n, budget, WINDOW)
    ck("the check coder.py uses today REFUSES that slice (this is the defect)",
       old_fits is False, f"prompt_fits said {old_fits}, est {old_est} tok, window {WINDOW}")


def test_the_fix_shows_the_real_target_suite():
    n = suite_chars()
    budget = coder.first_budget(BASE, WINDOW)
    fits, est, new_budget = coder.slice_fits(BASE, n, budget, WINDOW)
    ck("slice_fits fits the 7 KB target suite beside a 30 KB prompt in a 262k window",
       fits is True, f"est={est}")
    ck("  the reply keeps room: the budget returned is first_budget() of the GROWN prompt",
       new_budget == min(budget, coder.first_budget(BASE + n, WINDOW)),
       f"{new_budget} vs {min(budget, coder.first_budget(BASE + n, WINDOW))}")
    ck("  and it is smaller than the budget we entered with (the prompt pays for the slice)",
       new_budget < budget, f"{new_budget} vs {budget}")
    ck("  it is never negative", new_budget >= 0, f"{new_budget}")


def test_the_reserve_is_a_cap_not_a_floor():
    """A post-resume budget of 4000 must not be inflated to 32000 by the reserve."""
    n = suite_chars()
    fits, est, new_budget = coder.slice_fits(BASE, n, 4000, WINDOW)
    ck("a 4000-token reply still fits beside the prompt plus the 7 KB slice",
       fits is True, f"est={est}")
    ck("  and the budget passed in is respected, not raised to FILE_REPLY_RESERVE",
       new_budget == min(4000, coder.first_budget(BASE + n, WINDOW)), f"{new_budget}")


def test_the_guard_is_not_disabled():
    """The fix is a reserve, not a bypass: a slice the window genuinely cannot pay for is
    still refused, and the verdict tracks the SIZE rather than one placeholder value."""
    budget = coder.first_budget(BASE, WINDOW)
    fits_big, est_big, _ = coder.slice_fits(BASE, 1200000, budget, WINDOW)
    ck("a 1.2 MB slice is still refused", fits_big is False, f"est={est_big}")
    fits_real, _, _ = coder.slice_fits(BASE, suite_chars(), budget, WINDOW)
    ck("the verdict tracks the size -- the two slices are not one placeholder value",
       fits_real is not fits_big, f"{fits_real} vs {fits_big}")


def test_an_unknown_window_shrinks_nothing():
    """_win is None when settings is unreachable. first_budget returns MIN_TOKENS_FIRST
    there, so min(budget, first_budget(...)) would halve the reply budget on a machine
    that has room. An unknown window is not evidence -- prompt_fits says so."""
    n = suite_chars()
    fits, est, new_budget = coder.slice_fits(BASE, n, 250000, None)
    ck("an unknown window reports the slice as fitting", fits is True, f"est={est}")
    ck("  and leaves the reply budget alone", new_budget == 250000, f"{new_budget}")


def test_the_branch_that_refused_is_the_branch_that_is_fixed():
    """A helper that exists and is never called satisfies nothing
    (failure-schema: check-satisfied-by-dead-path)."""
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text(encoding="utf-8")
    ck("the refusal text is kept -- a reason in the log, not a silent skip",
       "no room for more of it beside the prompt" in src)
    i = src.index("no room for more of it beside the prompt")
    before, after = src[max(0, i - 900):i], src[i:i + 900]
    ck("the 'no room' guard is computed by slice_fits", "slice_fits(" in before,
       "no slice_fits( in the 900 characters before the refusal")
    ck("the serve path adopts the budget slice_fits returns",
       "new_budget" in after and "budget = " in after,
       "the slice branch does not lower the reply budget after showing bytes")
    j = src.index("def slice_fits")
    body = src[j:src.find("\ndef ", j + 1)]
    ck("slice_fits pays FILE_REPLY_RESERVE, not the whole reply budget",
       "FILE_REPLY_RESERVE" in body, body[:160])
    ck("the new-file branch's reference check is untouched",
       "prompt_fits(total_now, min(budget, FILE_REPLY_RESERVE), _win)" in src)


def main():
    for fn in [test_the_refusal_is_reproducible_with_the_module_s_own_arithmetic,
               test_the_fix_shows_the_real_target_suite,
               test_the_reserve_is_a_cap_not_a_floor,
               test_the_guard_is_not_disabled,
               test_an_unknown_window_shrinks_nothing,
               test_the_branch_that_refused_is_the_branch_that_is_fixed]:
        print(f"\n{fn.__name__}")
        try:
            fn()
        except Exception as e:            # noqa: BLE001 -- a raised test is a failed test
            ck("the test ran", False, f"{type(e).__name__}: {e}")
    print("\n" + ("FAIL: " + "; ".join(FAILS) if FAILS
                  else "t1004: the slice branch measures the reserve, not the whole budget"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
