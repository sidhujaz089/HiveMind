#!/usr/bin/env python3
"""t1597: the guard's two-level split -- pause, wait, shed, resume -- as data it re-reads.

ONE number used to do two jobs: at 2 GB the guard shed tests and builds AND decided to kill Swift, and
on 2026-10-24 it did exactly that -- shed everything cheaper at "below 2.0 GB at 1.42 GB free", then
the unit died anyway after 20 minutes of loading. Jas, 2026-10-25: pause 10, shed 8, resume 12, kill 2,
with the grace period adjustable in the same place as the levels.

What this oracle pins:
  1. THE LEVELS ARE DATA and are re-read when the file changes -- otherwise an edit on the Devices tab
     would need a restart, which is the thing Jas asked to be spared.
  2. THE ORDER IS ENFORCED: kill < shed < pause, and resume above pause, or the guard flaps.
  3. THE GUARD NEVER LIFTS A PAUSE IT DID NOT SET. scheduler_commands.resume() deletes the flag
     whoever took it, so a guard that resumed blindly would silently restart Jas's own pause. That is
     the case worth a test: a human pause must survive both pause_line() and resume_line().

Hermetic: the pause flag is a TemporaryDirectory file, swapped in and restored in a finally.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import memory_guard as mg          # noqa: E402
import scheduler_commands as sc    # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    # 1. THE DECLARED NUMBERS.
    L = mg.levels()
    check("the live levels are Jas's: pause 10, shed 8, resume 12, kill 2",
          (L["pause_gb"], L["shed_gb"], L["resume_gb"], L["kill_gb"]) == (10.0, 8.0, 12.0, 2.0), L)
    check("the grace period is declared", L["grace_s"] in (30.0, 60.0), L["grace_s"])
    check("the kill line is still the 2 GB Jas recorded", L["kill_gb"] == 2.0, L["kill_gb"])

    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        # 2. RE-READ ON CHANGE -- the no-restart promise.
        f = d / "guard-tiers.json"
        f.write_text(json.dumps({"levels": {"pause_gb": 10, "shed_gb": 8, "resume_gb": 12,
                                            "kill_gb": 2}}))
        first = mg.levels(f)["pause_gb"]
        f.write_text(json.dumps({"levels": {"pause_gb": 7, "shed_gb": 5, "resume_gb": 9,
                                            "kill_gb": 2}}))
        os.utime(f, (0, 0) if False else None)
        second = mg.levels(f)["pause_gb"]
        check("an edited level is obeyed without a restart", (first, second) == (10.0, 7.0),
              (first, second))
        check("a missing file falls back to the default levels, not to nothing",
              mg.levels(d / "nope.json")["pause_gb"] == 10.0)

        # 3. THE PAUSE IS OUR FAULT ONLY.
        saved = sc.PAUSE_FLAG
        try:
            sc.PAUSE_FLAG = d / "paused.json"
            sc.PAUSE_FLAG.write_text(json.dumps({"who": "jas", "why": "his own pause"}))
            check("the guard does not overwrite a pause a person took",
                  mg.pause_line("test") is False, "pause_line overwrote it")
            check("  and does not lift it either", mg.resume_line("test") is False,
                  "resume_line lifted a human's pause")
            check("  the flag is still there, still his",
                  json.loads(sc.PAUSE_FLAG.read_text()).get("who") == "jas")

            sc.PAUSE_FLAG.write_text(json.dumps({"who": "memory-guard", "why": "10 GB"}))
            check("the guard lifts its OWN pause", mg.resume_line("test") is True)
            check("  and the flag is gone", not sc.PAUSE_FLAG.exists())
            check("with no flag, pausing works", mg.pause_line("10 GB") is True
                  and json.loads(sc.PAUSE_FLAG.read_text())["who"] == "memory-guard")
        finally:
            sc.PAUSE_FLAG = saved

    # 4. THE ORDER IS ENFORCED, and the live file satisfies it.
    check("the live file passes the level rules", mg.validate_tiers() == [], mg.validate_tiers())
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.json"
        p.write_text(json.dumps({"levels": {"pause_gb": 6, "shed_gb": 8, "resume_gb": 12,
                                            "kill_gb": 2},
                                 "tiers": [{"name": "tests", "processes": ["scripts/run_tests.py"],
                                            "signal": "group", "triggers": ["reserve"]},
                                           {"name": "model", "processes": ["swift"],
                                            "signal": "trip", "triggers": ["reserve"]}]}))
        probs = mg.validate_tiers(p, repo=ROOT)
        check("a shed line BELOW the kill line is a finding",
              any("kill < shed < pause" in x for x in probs), probs)
        p.write_text(json.dumps({"levels": {"pause_gb": 10, "shed_gb": 8, "resume_gb": 9,
                                            "kill_gb": 2},
                                 "tiers": [{"name": "tests", "processes": ["scripts/run_tests.py"],
                                            "signal": "group", "triggers": ["reserve"]},
                                           {"name": "model", "processes": ["swift"],
                                            "signal": "trip", "triggers": ["reserve"]}]}))
        probs = mg.validate_tiers(p, repo=ROOT)
        check("a resume line at or below the pause line is a finding",
              any("must be ABOVE pause_gb" in x for x in probs), probs)
        p.write_text(json.dumps({"tiers": [{"name": "model", "processes": ["swift"],
                                            "signal": "trip", "triggers": ["reserve"]}]}))
        probs = mg.validate_tiers(p, repo=ROOT)
        check("no levels block at all is a finding", any("no usable levels" in x for x in probs), probs)

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
