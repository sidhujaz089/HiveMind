#!/usr/bin/env python3
"""The red suites can be grouped by shared cause, in one command (E1c, t8713).

E1 is "classify the 354 by shared cause". E1a widened what a failing suite stores, because the four-line
tail cut the cause off (37 of 318 had no error line at all). The digests only change when those suites RUN
again, so the classification has been waiting on a sweep.

This is the tool that makes the waiting worth it: the moment the sweep lands, `suite_verdicts.py --classify`
answers the question instead of someone writing a fourth throwaway classifier -- which is exactly how the
114-green mistake happened on 2026-10-26.

Two rules, and both are about not repeating that mistake:

  * the VERDICT still comes from the ledger (`suite-state.jsonl`), never from the cache;
  * the CAUSE comes from the cache's `out` field, and only because that is where the stored output lives.
    The two instruments are named separately in the output so nobody confuses which answered what.

What this refuses: a cache entry with no readable cause being silently dropped from the count (it becomes
its own bucket, so the numbers add up to the red count); and a "cause" that is really a tally line --
"3 passed, 2 failed" is not a cause and must not become the biggest cluster.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import suite_verdicts as sv  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


OUTS = {
    "a.py": "ok\nTraceback (most recent call last):\nModuleNotFoundError: No module named 'yaml'\n3 passed, 2 failed",
    "b.py": "ok\nModuleNotFoundError: No module named 'yaml'\n1 passed, 1 failed",
    "c.py": "ok\nAttributeError: 'NoneType' object has no attribute 'strip'\n2 passed, 1 failed",
    "d.py": "ok\n12 passed, 3 failed",
    "e.py": "",
}


def main():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        cache = d / "suite-cache.json"
        cache.write_text(json.dumps({k: {"red": True, "out": v} for k, v in OUTS.items()}), encoding="utf-8")
        groups = sv.cause_groups(path=cache)

        # 1. THE LARGEST CLUSTER IS THE SHARED CAUSE, not the tally.
        top = groups[0]
        check("the largest group is the repeated cause", top["cause"].startswith("ModuleNotFoundError"), top)
        check("and it counts both suites that share it", top["count"] == 2, top["count"])
        check("it names them", sorted(top["suites"]) == ["a.py", "b.py"], top["suites"])

        # 2. A TALLY IS NOT A CAUSE. 'd.py' prints only a tally; it must land in the unreadable bucket
        #    rather than becoming a cause of its own and topping the list.
        all_causes = " ".join(g["cause"] for g in groups)
        check("a tally line is not treated as a cause", "passed, 3 failed" not in all_causes, all_causes[:200])
        check("a suite with no cause line lands in its own bucket",
              any("no cause" in g["cause"].lower() for g in groups), [g["cause"] for g in groups])

        # 3. THE NUMBERS ADD UP. A silent drop is how a count becomes a lie.
        check("every red suite is counted exactly once",
              sum(g["count"] for g in groups) == len(OUTS), sum(g["count"] for g in groups))

        # 4. THE OUTPUT NAMES BOTH INSTRUMENTS, so nobody confuses which answered what.
        rendered = sv.classify_text(groups=groups, red=len(OUTS))
        check("the report says the verdict came from the ledger", "suite-state.jsonl" in rendered, rendered[:200])
        check("and the cause came from the cache's stored output", "suite-cache.json" in rendered, rendered[:400])

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
