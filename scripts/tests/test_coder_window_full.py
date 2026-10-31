#!/usr/bin/env python3
"""A full context window is not an output cut-off, and must not be retried as one.

p20, 2026-09-23: prompt 52,538 + output 12,996 = 65,534 of a 65,536 window. Ollama says
done_reason="length" for both cases; coder.py read it as the OUTPUT budget, retried at
MAX_TOKENS_RETRY (32000), and hit 52,163 + 13,371 = 65,534 again. Pinned here:

  * window_full() tells the two apart from the numbers Ollama returns
  * an unknown number is not evidence of a full window
  * ask() raises WindowFull (a Truncated, so old handlers still catch it) when it is full
  * the loop catches WindowFull BEFORE Truncated, records WINDOW-FULL, and stops
"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    print("window_full() reads the numbers, not the done_reason:")
    report("p20 attempt 2 (52538 + 12996 of 65536) is a full window",
           coder.window_full(52538, 12996, 65536), True)
    report("p20 attempt 4 (52163 + 13371 of 65536, budget 32000) is a full window",
           coder.window_full(52163, 13371, 65536), True)
    report("a 7774-token prompt that spent a 16000 budget is NOT a full window",
           coder.window_full(7774, 16000, 65536), False)
    report("an unknown prompt size is not evidence", coder.window_full(None, 13000, 65536), False)
    report("an unknown window is not evidence", coder.window_full(52000, 13000, None), False)

    print("\nWindowFull is a Truncated that carries the window:")
    e = coder.WindowFull(12996, 530.0, {}, 52538, 65536)
    report("it is a Truncated, so every existing handler still catches it",
           isinstance(e, coder.Truncated), True)
    report("  and carries prompt size and window", (e.prompt_tokens, e.window), (52538, 65536))

    print("\nask() raises it, and the loop stops on it:")
    asrc = inspect.getsource(coder.ask)
    report("ask() checks the window before raising Truncated",
           0 < asrc.find("raise WindowFull(") < asrc.rfind("raise Truncated(d.get"), True)
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    wf, tr = src.find("except WindowFull as e:"), src.find("except Truncated as e:")
    report("the loop catches WindowFull before Truncated", 0 < wf < tr, True)
    block = src[wf:tr]
    report("  records WINDOW-FULL", "\"WINDOW-FULL\"" in block, True)
    report("  and stops instead of retrying", "\n                break\n" in block, True)
    report("  and does not raise the budget", "retry_budget" not in block, True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
