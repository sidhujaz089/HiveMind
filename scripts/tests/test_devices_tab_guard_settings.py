#!/usr/bin/env python3
"""t1597: the Devices tab -- the guard's levels you can edit, and the log that says why it fired.

Jas, 2026-10-25: "The levels you recommended are good but I want a Devices tab where I can adjust these
types of settings. If I adjust things the systems effected should reboot on their own if possible so I
dont need to enter terminal commands. In the same area I want to see logs of what was going on when
guards were triggered so I can judge how to optimize."

So the page is two halves and this oracle tests both, plus the promise that makes it safe:
  1. SAVING NEEDS NO RESTART. What is written is the file memory_guard.levels() re-reads on its next
     tick, so the test asserts the guard sees the new number immediately -- not that a service was
     restarted (there is nothing to restart).
  2. A REFUSAL WRITES NOTHING. The order is the safety property: kill < shed < pause, resume above
     pause. An inverted set must leave the file byte-identical, because a half-written guard config is
     a guard that sheds nothing and then kills the model.
  3. THE LOG IS READ, not imagined: a trigger row from the ledger appears with its free GB, the tier
     it shed and what that recovered.

Hermetic: every file is under a TemporaryDirectory, seeded from the live guard-tiers.json so the tier
list is the real one.
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import feedback as fb        # noqa: E402
import memory_guard as mg    # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def form(**kw):
    return {k: [str(v)] for k, v in kw.items()}


def main():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        cfg = d / "guard-tiers.json"
        shutil.copy(ROOT / "workspace" / "guard-tiers.json", cfg)

        # 1. SAVE, AND THE GUARD OBEYS WITHOUT A RESTART.
        probs = fb.save_guard_levels(form(pause_gb=9, shed_gb=6, resume_gb=11, kill_gb=2,
                                          grace_s=45), path=cfg)
        check("a consistent set saves", probs == [], probs)
        seen = mg.levels(cfg)
        check("the guard reads the new numbers on its next tick (no restart)",
              (seen["pause_gb"], seen["shed_gb"], seen["resume_gb"], seen["grace_s"])
              == (9.0, 6.0, 11.0, 45.0), seen)
        kept = json.loads(cfg.read_text())
        check("the tier order and processes are left alone",
              [t["name"] for t in kept["tiers"]] == ["tests", "builds", "model"], kept["tiers"])

        # 2. A REFUSAL WRITES NOTHING.
        before = cfg.read_bytes()
        for label, bad in (("shed below kill", dict(pause_gb=10, shed_gb=1, resume_gb=12, kill_gb=2)),
                           ("resume at pause", dict(pause_gb=10, shed_gb=8, resume_gb=10, kill_gb=2)),
                           ("resume below pause", dict(pause_gb=10, shed_gb=8, resume_gb=5, kill_gb=2))):
            probs = fb.save_guard_levels(form(**bad), path=cfg)
            check(f"refused: {label}", bool(probs), probs)
            check(f"  and the file is untouched ({label})", cfg.read_bytes() == before)
        probs = fb.save_guard_levels({"pause_gb": ["ten"]}, path=cfg)
        check("refused: a word where a number belongs", any("must be a number" in x for x in probs),
              probs)
        check("  and still untouched", cfg.read_bytes() == before)

        # 3. THE PAGE SHOWS THE NUMBERS, THE ORDER AND THE FORM TARGET.
        page = fb.devices_page(path=cfg, ledger=d / "no-ledger.jsonl")
        check("the form posts to the settings route", 'action="/devices/settings"' in page)
        check("every level is an editable field",
              all(f'name="{k}"' in page for k in fb.LEVEL_FIELDS), page[:200])
        check("every timer is an editable field",
              all(f'name="{k}"' in page for k in fb.TIMER_FIELDS), page[:200])
        check("the shed order is shown, model last", "tests" in page and "model" in page)
        check("with no trips it says so rather than showing an empty table",
              "has not fired" in page, page[-200:])

        # 4. THE LOG IS READ FROM THE LEDGER.
        led = d / "memory-guard.jsonl"
        led.write_text(json.dumps({"at": "2026-10-25T02:36:35Z", "available_gb": 1.66,
                                   "stopped": "tests", "pids": [154307],
                                   "available_after_gb": 4.1,
                                   "why": "available below 2.0 GB"}) + "\n")
        page = fb.devices_page(path=cfg, ledger=led)
        check("a real trigger row is shown with its numbers",
              "1.66" in page and "tests" in page and "4.1" in page and "below 2.0 GB" in page,
              page[-400:])

        # 5. A BROKEN CONFIG IS REPORTED, NOT RENDERED AS BLANK.
        cfg.write_text("{not json")
        page = fb.devices_page(path=cfg, ledger=led)
        check("an unreadable config is said out loud", "unreadable" in page, page[:120])

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
