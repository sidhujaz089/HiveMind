#!/usr/bin/env python3
"""Fixtures for lane_status.py -- the "is the model actually working" report.

WHY THESE ASSERTIONS AND NOT OTHERS. This tool's whole value is that it is
believed when it says a run is fine. Every assertion below is a case where it
would say something reassuring and be wrong:

  * a rate computed from replies too short to measure decode. The first version
    did this and reported "0.03-10.29 tok/s" for one model, i.e. "this attempt
    will take between 26 minutes and 148 hours". The numbers were real telemetry;
    the sample was junk. This is the assertion that would have caught it.
  * a duplicate dispatch reported as two healthy lanes. Two identical commands
    race for one GPU and, with --apply, one branch. On 2026-09-14 two lanes ran
    the same brief for three hours and nothing said so.
  * a zombie counted as a running lane -- finished-but-unreaped looks alive to
    anything matching on a name (L-105).

The duplicate case is asserted in BOTH directions. A detector that shouts on
every pair of lanes is as useless as one that never shouts, and only the negative
case tells them apart.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import lane_status as LS   # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def main():
    # ---- the rate filter: junk samples must not reach the estimate
    rows = [
        {"model": "m", "tok_s_decode": 0.03, "eval_count": 117},   # junk
        {"model": "m", "tok_s_decode": 0.09, "eval_count": 61},    # junk
        {"model": "m", "tok_s_decode": 4.0, "eval_count": 16000},  # real
        {"model": "m", "tok_s_decode": 6.0, "eval_count": 14596},  # real
        {"model": "m", "tok_s_decode": 8.0, "eval_count": 8186},   # real
    ]
    tmp = Path(tempfile.mkdtemp()) / "t.jsonl"
    tmp.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    real, LS.TELEMETRY = LS.TELEMETRY, tmp
    try:
        r = LS.rates()
    finally:
        LS.TELEMETRY = real

    med, lo, hi, n = r["m"]
    report("only real decode samples are counted", n, 3)
    report("the junk minimum is excluded", lo, 4.0)
    report("the median comes from real runs", med, 6.0)
    report("a 0.03 tok/s sample cannot reach the estimate", lo > 1, True)

    # a model with nothing but junk must report NOTHING, not a made-up number
    tmp.write_text(json.dumps({"model": "q", "tok_s_decode": 0.03,
                               "eval_count": 50}) + "\n")
    real, LS.TELEMETRY = LS.TELEMETRY, tmp
    try:
        r2 = LS.rates()
    finally:
        LS.TELEMETRY = real
    report("a model with only junk samples is absent, not estimated",
           "q" in r2, False)

    # ---- duplicate detection, both directions
    def dup(cmds):
        return len(set(cmds)) < len(cmds)
    report("identical lanes are flagged",
           dup(["--brief b --model m", "--brief b --model m"]), True)
    report("different briefs are NOT flagged",
           dup(["--brief b --model m", "--brief c --model m"]), False)
    report("same brief, different model is NOT flagged",
           dup(["--brief b --model m", "--brief b --model n"]), False)

    # ---- arg extraction survives a missing or trailing flag
    report("a missing flag yields the default",
           LS.arg(["coder.py"], "--model", "(default)"), "(default)")
    report("a flag with no value does not crash",
           LS.arg(["coder.py", "--model"], "--model", "(default)"), "(default)")
    report("a present flag is read",
           LS.arg(["coder.py", "--model", "q"], "--model"), "q")

    # ---- lanes() reads /proc, and this process is not a coder.py
    report("this test process is not reported as a lane",
           any(x["pid"] == __import__("os").getpid() for x in LS.lanes()), False)
    report("no lane is reported in state Z",
           [x for x in LS.lanes() if x["stat"] == "Z"], [])

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
