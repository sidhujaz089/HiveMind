#!/usr/bin/env python3
"""The memory guard stops the cheapest work first (Jas, 2026-10-18: "I agree it should kill better").

Swift was the guard's only victim and the dearest one (~13 min to reload). At the one trigger (RESERVE_GB, 2 GB) the guard now
stops test runs, then builds, one tier per step, and kills Swift only if memory is still below it. Nothing here signals a real process or touches a real ledger."""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_guard as g  # noqa: E402

failed = 0


def check(name, ok):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


with tempfile.TemporaryDirectory() as t:
    ledger = Path(t) / "guard.jsonl"

    # 1. cheapest first: tests are stopped and builds are not touched in the same step
    calls = []

    def stop(tier):
        calls.append(tier[0])
        return [111] if tier[0] == "tests" else [222]

    row = g.shed(3.0, ledger=ledger, stop=stop, measure=lambda: 9.0, settle=0)
    check("the first tier with anything running is tests", row and row["stopped"] == "tests")
    check("and only that tier is stopped in one step", calls == ["tests"])
    check("the row records before and after", row["available_gb"] == 3.0 and row["available_after_gb"] == 9.0)

    # 2. no tests running -> builds are next
    calls.clear()
    row = g.shed(3.0, ledger=ledger, stop=lambda tier: (calls.append(tier[0]), [] if tier[0] == "tests" else [222])[1],
                 measure=lambda: 8.0, settle=0)
    check("with no test run, builds are stopped", row and row["stopped"] == "builds" and calls == ["tests", "builds"])

    # 3. nothing cheaper left -> shed returns None, so the loop falls through to the Swift trip
    check("nothing cheaper to stop returns None", g.shed(1.5, ledger=ledger, stop=lambda tier: [], settle=0) is None)
    rows = [json.loads(x) for x in ledger.read_text().splitlines()]
    check("one ledger row per stopped tier", [r["stopped"] for r in rows] == ["tests", "builds"])

    # 4. tier membership is read from /proc cmdlines, and never matches the guard itself
    proc = Path(t) / "proc"
    for pid, cmd in ((10, "python3 scripts/run_tests.py --cache"), (11, "python3 projects/local-inference/coder.py --brief x"),
                     (12, "python3 scripts/dispatch_loop.py"), (13, "python3 scripts/run_tests.py")):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "cmdline").write_bytes(cmd.replace(" ", "\0").encode())
    real = g._cmdline
    g._cmdline = lambda pid: (proc / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode()
    try:
        check("test runs are found by cmdline", sorted(g.tier_pids(g.TIERS[0][1], proc=str(proc), me=13)) == [10])
        check("  and the guard's own pid is never in the list", 13 not in g.tier_pids(g.TIERS[0][1], proc=str(proc), me=13))
        check("builds are found by cmdline", g.tier_pids(g.TIERS[1][1], proc=str(proc), me=0) == [11])
        check("the scheduler is in no tier", 12 not in g.tier_pids(g.TIERS[0][1] + g.TIERS[1][1], proc=str(proc), me=0))
    finally:
        g._cmdline = real

    # 5. how each tier is stopped: tests by process group, builds by SIGTERM to the pid (checkpoint)
    groups, terms = [], []
    g.stop_tier(("tests", ("x",), "group"), find=lambda m: [7], signal_group=groups.append, signal_pid=terms.append)
    g.stop_tier(("builds", ("x",), "term"), find=lambda m: [8], signal_group=groups.append, signal_pid=terms.append)
    check("tests are stopped as a group, builds by SIGTERM", groups == [7] and terms == [8])
    check("one trigger: there is no separate soft line", not hasattr(g, "SOFT_GB") and g.RESERVE_GB == 2.0)

# 6. memory pressure (2026-10-19): read as oomd reads it, and the shed line sits below oomd's 50%
with tempfile.TemporaryDirectory() as t2:
    pf = Path(t2) / "memory.pressure"
    pf.write_text("some avg10=41.50 avg60=20.00 avg300=5.00 total=1\nfull avg10=30.00 avg60=1 avg300=1 total=1\n")
    check("pressure() reads 'some avg10'", g.pressure(pf) == 41.5)
    check("an unreadable pressure file is None, not 0", g.pressure(Path(t2) / "missing") is None)
    check("pressure() reads 'some avg60' when asked", g.pressure(pf, window="avg60") == 20.0)
    conf = Path(t2) / "oomd.conf"
    conf.write_text("[Service]\nManagedOOMMemoryPressureLimit=80%\n")
    check("oomd's limit is read from its own config", g.oomd_limit(conf) == 80.0)
    check("and defaults to oomd's 50% when unset", g.oomd_limit(Path(t2) / "none") == 50.0)
# 2026-10-20: both shed lines sat under oomd's limit. 2026-10-23 (Jas: the guard's 10-second line at
# 80 and oomd at 90): the 10-second line moved to 80, which oomd's 80 HELD for 30 s still trails, and
# with oomd at 90 it is under it again. The 10-second line must
# never go ABOVE oomd's limit: then oomd would always act first and the guard would be decoration.
check("the 10-second shed line is at or under systemd-oomd's limit",
      g.PRESSURE_SHED <= g.oomd_limit())
check("a 36% spike (what stopped every hourly run on 10-20) is not a squeeze", 36 < g.PRESSURE_SHED)
check("nor a 63% one with the minute average at 27% (07:08 the same day)", 63 < g.PRESSURE_SHED and 27 < g.PRESSURE_SHED_60)
check("a minute at 40% with 18 GB free (10:01, 10:07) no longer sheds", 40 < g.PRESSURE_SHED_60)
check("the 07:21 oomd kill (avg10 87.5) is still caught by the 10-second line", 87.5 >= g.PRESSURE_SHED)

print("FAILED" if failed else "ok")
sys.exit(1 if failed else 0)
