#!/usr/bin/env python3
"""t1363 -- the memory-dip skip in restart_stale is RECORDED, not just taken.

The class this pins (RCA 668cf52495df, session 4d79bb3a): the agent said memory-guard will
not be restarted inside a dip, shipped it with the oracle that exercised that claim RED
(`AttributeError: module 'restart_stale' has no attribute 'hold_restart'`, transcript 658),
and declared success off the unrelated stale-restart path (`[ok]`, line 879). The mechanism
arrived in commit 63c5c544. The PROOF it is in force at the next dip never did: the RCA's
DE2 says of the dip-skip, "the only evidence is the assertion".

So this suite asks the one thing the RCA's refused anti-pattern lacked: can a recorder show
the skip was in force? `restart_stale.hold_restart(unit, available)` appends one row per call
to module-level `HOLD_DECISIONS`, naming the unit, the verdict, the number the decision was
taken on, and the reason it returned -- EVERY call, including the units that today return at
line 48 before any recording is possible.

Hermetic: a fake `memory_guard` is installed in sys.modules before `restart_stale` is
imported, so `hold_restart`'s inner `import memory_guard` binds the fake and no live memory
is read. Nothing is written to disk.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]

FAKE_MG = types.ModuleType("memory_guard")
FAKE_MG.RESERVE_GB = 2.0            # fixed, so thresholds are arithmetic, not machine state
FAKE_MG.available_gb = lambda: 99.0  # a machine with plenty of room
sys.modules["memory_guard"] = FAKE_MG

import restart_stale as R  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"  (got {got!r}, want {want!r})"))


def test_recorder_exists_and_starts_empty():
    rows = getattr(R, "HOLD_DECISIONS", None)
    report("HOLD_DECISIONS is a list on the module", isinstance(rows, list), True)
    if isinstance(rows, list):
        report("no decision is recorded before any call", len(rows), 0)


def test_a_dip_is_recorded_as_a_held_decision():
    rows = R.HOLD_DECISIONS
    rows.clear()
    reason = R.hold_restart("memory-guard.service", FAKE_MG.RESERVE_GB + 1)
    report("a dip returns a reason (the unit is skipped)", bool(reason), True)
    report("exactly one row recorded", len(rows), 1)
    row = rows[0] if rows else {}
    report("the row names the unit it decided on", row.get("unit"), "memory-guard.service")
    report("the row says the unit is held", row.get("held"), True)
    report("the row records the number the decision used",
           row.get("available_gb"), round(FAKE_MG.RESERVE_GB + 1, 1))
    report("the row carries the reason the function returned", row.get("reason"), reason)


def test_roomy_memory_is_recorded_as_not_held():
    rows = R.HOLD_DECISIONS
    rows.clear()
    reason = R.hold_restart("memory-guard.service", FAKE_MG.RESERVE_GB + 10)
    report("roomy memory returns no reason", reason, "")
    report("exactly one row recorded", len(rows), 1)
    row = rows[0] if rows else {}
    report("the row names the unit", row.get("unit"), "memory-guard.service")
    report("the row says the unit is NOT held", row.get("held"), False)
    report("a not-held row records no reason", row.get("reason"), "")
    report("the row records the number the decision used",
           row.get("available_gb"), round(FAKE_MG.RESERVE_GB + 10, 1))


def test_a_unit_that_cannot_be_held_is_still_recorded():
    # Today `unit != "memory-guard.service"` returns at restart_stale.py:48, before any
    # recording is possible. A recorder written only into the dip branch passes every other
    # test in this file and fails this one.
    rows = R.HOLD_DECISIONS
    rows.clear()
    report("a non-memory unit is never held", R.hold_restart("thinkpad-guard.service", 0.1), "")
    report("its decision is recorded too", len(rows), 1)
    row = rows[0] if rows else {}
    report("the row names the unit", row.get("unit"), "thinkpad-guard.service")
    report("the row says the unit is NOT held", row.get("held"), False)
    report("the row records the number it was given", row.get("available_gb"), 0.1)


def test_the_number_recorded_is_the_one_the_decision_used():
    # available=None sends the call through memory_guard.available_gb() -- the fake's 99.0.
    rows = R.HOLD_DECISIONS
    rows.clear()
    report("a roomy reading returns no reason", R.hold_restart("memory-guard.service"), "")
    report("exactly one row recorded", len(rows), 1)
    row = rows[0] if rows else {}
    report("the row records available_gb()'s reading", row.get("available_gb"), 99.0)
    report("and marks it not held", row.get("held"), False)


def test_the_return_contract_is_unchanged():
    # The frozen suite scripts/tests/test_restart_stale_guards.py pins these three return
    # values (lines 28-30). This suite must not cost them their meaning.
    rows = R.HOLD_DECISIONS
    rows.clear()
    report("dip -> truthy reason", bool(R.hold_restart("memory-guard.service",
                                                       FAKE_MG.RESERVE_GB + 1)), True)
    report("room -> ''", R.hold_restart("memory-guard.service", FAKE_MG.RESERVE_GB + 10), "")
    report("other unit -> ''", R.hold_restart("thinkpad-guard.service", 0.1), "")
    report("all three calls were recorded", len(rows), 3)


TESTS = [test_recorder_exists_and_starts_empty,
         test_a_dip_is_recorded_as_a_held_decision,
         test_roomy_memory_is_recorded_as_not_held,
         test_a_unit_that_cannot_be_held_is_still_recorded,
         test_the_number_recorded_is_the_one_the_decision_used,
         test_the_return_contract_is_unchanged]


def main():
    global PASSED, FAILED
    for t in TESTS:
        print(t.__name__)
        try:
            t()
        except Exception as e:  # reported, never swallowed: the reason IS the finding
            FAILED += 1
            print(f"  FAIL  {t.__name__} raised {type(e).__name__}: {e}")
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
