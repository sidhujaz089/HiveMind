#!/usr/bin/env python3
"""A preflight launch that never got a process cannot count as a review that "ended without a verdict".

RCA 2026-10-28 (workspace/rca/rca-no-verdict-preflight-holds-2026-10-28.md, RC-B). The dispatcher holds a
brief when `runs - recorded_objeeeections >= 2`, and a REFUSED DUPLICATE LAUNCH is written with pid 0, no
exit and no duration. The old test read "no duration" as "ran at least five seconds", so two refused
launches were enough to hold a brief as "the reviewer is broken" without the reviewer ever being asked.

Real rows for p840-t1522-ask-q16 (workspace/dispatch-loop.jsonl), which is the fixture below:

    18:43:53Z  exit 4     seconds 1    pid 3317347
    18:45:01Z  exit None  seconds None pid 0  duplicate_of 3343951   <- refused
    18:45:01Z  exit None  seconds None pid 0  duplicate_of 3343951   <- refused
    18:45:02Z  exit 4     seconds 1    pid 3343951
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
SHA = "abc123"


def row(**kw):
    base = {"event": "finished", "kind": "preflight", "brief_sha256": SHA}
    base.update(kw)
    return base


def main():
    import dispatch_loop as D
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    # THE LIVE SHAPE: two refused launches, no objection rows.
    refused = [row(pid=0, exit=None, seconds=None, duplicate_of=3343951),
               row(pid=0, exit=None, seconds=None, duplicate_of=3343951)]
    check("two REFUSED launches are not two reviews", D.preflight_crashes(refused, [], SHA) == 0,
          D.preflight_crashes(refused, [], SHA))

    # A REAL run that recorded nothing IS a crash, and must still count -- or this fix hides the
    # reviewer-broken case it exists to report.
    real = [row(pid=1234, exit=2, seconds=20), row(pid=1235, exit=2, seconds=14)]
    check("two REAL runs with no objection still count as two crashes",
          D.preflight_crashes(real, [], SHA) == 2, D.preflight_crashes(real, [], SHA))

    check("  one real run plus its recorded objection is not a crash",
          D.preflight_crashes(real[:1], [{"brief_sha256": SHA}], SHA) == 0,
          D.preflight_crashes(real[:1], [{"brief_sha256": SHA}], SHA))
    check("  a yielded run (exit 4) is not a review",
          D.preflight_crashes([row(pid=9, exit=4, seconds=1)], [], SHA) == 0, "")
    check("  a sub-5-second run is not a review",
          D.preflight_crashes([row(pid=9, exit=1, seconds=0)], [], SHA) == 0, "")
    check("  rows for a different brief do not count",
          D.preflight_crashes([row(pid=9, exit=2, seconds=30, brief_sha256="other")], [], SHA) == 0, "")
    check("  the live mixture: 2 refused + 1 real, no objections -> exactly 1 crash",
          D.preflight_crashes(refused + real[:1], [], SHA) == 1,
          D.preflight_crashes(refused + real[:1], [], SHA))
    print("test_refused_launch_is_not_a_review:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
