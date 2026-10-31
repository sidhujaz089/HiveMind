#!/usr/bin/env python3
"""settings.request() must REFUSE an unparseable keep_alive, naming the setting.

Run:  python3 scripts/tests/test_keepalive_validation.py

WHY, t287. On 2026-09-12 `keep_alive` was saved as the string "-1" from the console. It is a
FREE-TEXT field, so nothing objected. `-1` IS ollama's convention for keep-loaded-forever -- as
an INTEGER -- and quoted, this build refuses the whole request:

    HTTP 400 {"error":"time: missing unit in duration \\"-1\\""}

Every call through settings.request() failed instantly for a weekend: preflight_brief, coder.py,
ask_q16, both cron timers. Nothing on the GPU side was wrong. It took a hand trace to find,
because the error names a DURATION and not a SETTING -- the layer that knew which profile and
which field was involved threw that context away and passed the server's string up.

THE CONTRACT THIS PINS, and both halves are load-bearing:

  * A BAD DURATION IS REFUSED BEFORE THE REQUEST IS SENT, with a message naming `keep_alive`
    and the profile. Failing at the socket is what made this expensive.
  * EVERY GOOD DURATION STILL PASSES. A validator that rejects too much gets removed within a
    week (AGENTS.md rule 9), and the legitimate forms are ollama's: a number of seconds, or a
    duration with a unit -- "24h", "5m", "30s", "0", "-1s". `0` means unload immediately and
    inference_health uses it, so rejecting it would break the health check.

The asymmetry is deliberate. This is a five-line check standing in front of a fault that cost a
weekend, so it fails CLOSED on anything it cannot parse.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))

import settings                                                      # noqa: E402

PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: got {got!r}, want {want!r}")


def refused(value):
    """(was_refused, message) for a keep_alive value, without touching the real settings file."""
    if not hasattr(settings, "validate_keep_alive"):
        return None, "settings.validate_keep_alive does not exist"
    try:
        settings.validate_keep_alive(value, section="coder")
        return False, ""
    except Exception as e:                                           # noqa: BLE001
        return True, f"{type(e).__name__}: {e}"


def main():
    check("the validator exists at settings.validate_keep_alive",
          hasattr(settings, "validate_keep_alive"), True)
    if not hasattr(settings, "validate_keep_alive"):
        print("\ntest_keepalive_validation: 1 failed (nothing else could be tested)")
        return 1

    # --- THE VALUE THAT COST A WEEKEND, and its neighbours
    for bad in ("-1", "1", "forever", "", "24", "h", "24 h", "-", "1.5"):
        was, msg = refused(bad)
        check(f"refuses keep_alive={bad!r}", was, True)
        if was:
            check(f"  ...and the message names keep_alive, not just a duration",
                  "keep_alive" in msg, True)

    # --- EVERY LEGITIMATE FORM STILL PASSES. A validator that over-refuses gets deleted.
    for good in ("24h", "5m", "30s", "0", "-1s", "1h30m", 0, 300, -1):
        was, msg = refused(good)
        check(f"accepts keep_alive={good!r}", was, False)

    # --- AND IT IS WIRED IN, not merely available. t287's whole shape is a check that exists
    #     and is not called; gates 3 and 6 stayed open for exactly that reason.
    src = (REPO / "projects" / "local-inference" / "settings.py").read_text(encoding="utf-8")
    body = src.split("def request(", 1)[-1].split("\ndef ", 1)[0] if "def request(" in src else ""
    check("request() calls the validator", "validate_keep_alive" in body, True)

    print(f"\ntest_keepalive_validation: {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
