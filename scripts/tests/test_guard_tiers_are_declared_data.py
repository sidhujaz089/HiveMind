#!/usr/bin/env python3
"""t1597: the memory guard's shed order is declared data, and moving it there changed no behaviour.

The order used to be a tuple in memory_guard.py: tests, then builds, then the model. Jas, 2026-10-09:
"Keep the trigger at 2GB but kill processes in the right order." The model goes LAST because Swift
takes about twenty minutes to reload -- on 2026-10-24 the guard shed tests and builds repeatedly and
then the unit died anyway, which is what t1597 is about.

This oracle pins the two halves that matter:
  1. THE ORDER IS THE SAME AS BEFORE -- the exact tuple, the exact names, the exact triggers. A data
     move that quietly reorders the shedding is a behaviour change wearing a refactor's clothes.
  2. THE DATA IS CHECKED, NOT TRUSTED -- a process that no longer exists (a rename would silently
     stop that tier being shed), the model not last, an unknown trigger, no tiers at all. And a
     MISSING file degrades to the built-in order with a named problem rather than stopping the guard:
     a memory guard that will not start because JSON is absent is worse than one running last week's
     order, which is why the fallback exists and why check_workspace fails when the file is unusable.

Hermetic: the fixtures are TemporaryDirectories; nothing is shed, nothing is stopped.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import memory_guard as mg  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def fixture(d, obj):
    p = Path(d) / "guard-tiers.json"
    p.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    return p


def tier(name, procs, sig="term", trig=("reserve",)):
    return {"name": name, "processes": list(procs), "signal": sig, "triggers": list(trig)}


def main():
    # 1. BEHAVIOUR IS UNCHANGED. These are the values the hardcoded tuple carried.
    check("the tests tier is first and fires on the reserve only",
          mg.TIERS[0] == ("tests", ("scripts/run_tests.py", "scripts/hourly_checks.py"),
                          "group", ("reserve",)), mg.TIERS[0])
    check("the builds tier is second and honours both triggers",
          mg.TIERS[1] == ("builds", ("projects/local-inference/coder.py",), "term",
                          ("reserve", "pressure")), mg.TIERS[1])
    check("the model is still the thing killed last, and still swift",
          mg.CONTAINERS == ("swift",), mg.CONTAINERS)
    check("the reserve is still Jas's 2 GB", mg.RESERVE_GB == 2, mg.RESERVE_GB)
    check("the live order came from the declared file, not the fallback",
          mg.TIERS_SOURCE == "file", (mg.TIERS_SOURCE, mg.TIERS_PROBLEM))

    # 2. THE LIVE FILE ITSELF.
    check("the live workspace/guard-tiers.json validates clean", mg.validate_tiers() == [],
          mg.validate_tiers())

    with tempfile.TemporaryDirectory() as d:
        # 3. A MISSING FILE: the guard keeps working, and says why it is not using the declared order.
        tiers, model, reserve, source, problem = mg.load_tiers(Path(d) / "nope.json")
        check("a missing file degrades to the built-in order", tiers == mg.DEFAULT_TIERS, tiers)
        check("  and says where the order came from", source == "defaults", source)
        check("  and names the problem rather than being quiet", bool(problem), problem)
        check("  and check_workspace can see it is not the declared order",
              bool(mg.validate_tiers(Path(d) / "nope.json")), "validate returned nothing")

        # 4. THE MODEL NOT LAST.
        p = fixture(d, {"reserve_gb": 2, "tiers": [tier("model", ["swift"], "trip"),
                                                   tier("tests", ["scripts/run_tests.py"], "group")]})
        probs = mg.validate_tiers(p, repo=ROOT)
        check("the model placed before another tier is a finding",
              any("LAST" in x for x in probs), probs)

        # 5. A PROCESS THAT NO LONGER EXISTS -- the silent-rename failure.
        p = fixture(d, {"tiers": [tier("tests", ["scripts/run_tests.py", "scripts/gone_forever.py"]),
                                  tier("model", ["swift"], "trip")]})
        probs = mg.validate_tiers(p, repo=ROOT)
        check("a tier naming a missing process is a finding",
              any("does not exist" in x for x in probs), probs)

        # 6. AN UNKNOWN TRIGGER, AND NO TIERS AT ALL.
        p = fixture(d, {"tiers": [tier("tests", ["scripts/run_tests.py"], trig=["vibes"]),
                                  tier("model", ["swift"], "trip")]})
        probs = mg.validate_tiers(p, repo=ROOT)
        check("an unknown trigger is a finding", any("vibes" in x for x in probs), probs)
        p = fixture(d, {"tiers": []})
        check("an empty tier list is a finding", bool(mg.validate_tiers(p, repo=ROOT)), "nothing")
        p = fixture(d, "{not json")
        check("unparseable JSON is a finding", bool(mg.validate_tiers(p, repo=ROOT)), "nothing")

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
