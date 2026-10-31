#!/usr/bin/env python3
"""t477: the builder's first-attempt output budget is 32,000 when the window holds it.

WHY. p53 on gemma4:26b was cut off at 16,000 tokens on attempts 2 and 3 while writing a whole
coder.py. The 16,000 cap dated from 2026-09-12, when the window was 32,768 and a 32,000 budget
left 768 tokens for the prompt. The window is 65,536 now. Jas, 2026-09-30: "Yes raise it."

The floor stays 16,000 (the fit check refuses below it), a window without room gets the floor,
and an unknown window gets the floor -- the raise is only for room we can see.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


# ~10k-token prompt (3 chars/token) in a 65,536 window: 10k + 32k + 2k margin fits.
check("small prompt, 65k window -> all the room (Jas 2026-10-06)", coder.first_budget(30_000, 65536), 65536 - 10_000 - 2000)
check("Swift 262k window -> all the room", coder.first_budget(30_000, 262144), 262144 - 10_000 - 2000)
# ~40k-token prompt: 40k + 32k does not fit, 40k + 16k does -> the floor.
check("large prompt, 65k window -> the room left", coder.first_budget(120_000, 65536), 65536 - 40_000 - 2000)
check("prompt fills the window -> the 16000 floor", coder.first_budget(180_000, 65536), 16000)
# The 09-12 case: a 32,768 window never gets 32,000.
check("32k window -> the room left", coder.first_budget(3_000, 32768), 32768 - 1000 - 2000)
check("unknown window -> 16000", coder.first_budget(3_000, None), 16000)
check("floor is 16000", coder.MIN_TOKENS_FIRST, 16000)
# retry_budget clamps to the window it is GIVEN, not the profile's 262,144 default.
check("retry_budget clamps to a 32k window",
      coder.retry_budget("x" * 35_000, num_ctx=32768) < 32000, True)

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
