#!/usr/bin/env python3
"""GATE: the guard's PSI pressure-shed must not kill the hourly sweep (Job B, 2026-10-23).

The RCA (dsh-week/B-hourly-checks-rca.md, 42/42 kills, 1:1 with "stopped tests" lines) and
the sweep review (dsh-week/8-sweep-review.md, option 5) settled the fix: the `tests` tier
scripts/run_tests.py + scripts/hourly_checks.py fires on the FREE-RAM RESERVE only. The
pressure path (user@1000.service PSI avg10>=75 / avg60>=60) is structurally high on the GB10
while 17-20 GB are free -- a real squeeze on Swift's weight residency is the free-RAM dip,
and the builds tier keeps the pressure trigger because builds are the expensive, deferrable
work the pressure path was built for (Jas, 2026-10-18: kill the cheapest work in the right
order; 2026-10-20: "A SPIKE IS NOT A SQUEEZE").

Hermetic: fake find, fake signal, temp ledger. Nothing real is signalled.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import memory_guard as mg

P = F = 0


def check(label, got, want):
    global P, F
    ok = got == want
    P += ok
    F += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f": got {got!r}, want {want!r}"))


# The shape: every tier declares which triggers may stop it.
def test_tier_shape():
    tiers = mg.TIERS
    check("two tiers, tests first", [t[0] for t in tiers], ["tests", "builds"])
    for t in tiers:
        check(f"tier {t[0]} declares its triggers (4th field)", len(t) >= 4 and isinstance(t[3], (tuple, list)), True)
    by = {t[0]: t for t in tiers}

    def triggers(name):
        t = by[name]
        return tuple(t[3]) if len(t) >= 4 else ()

    check("tests tier fires on the free-RAM reserve", "reserve" in triggers("tests"), True)
    check("tests tier does NOT fire on PSI pressure", "pressure" in triggers("tests"), False)
    check("builds tier still fires on PSI pressure", "pressure" in triggers("builds"), True)
    check("builds tier still fires on the reserve", "reserve" in triggers("builds"), True)


# stop_tier must keep working with the widened tuple.
def test_stop_tier_widened_tuple():
    seen = []
    tier = ("tests", ("scripts/hourly_checks.py",), "group", ("reserve",))
    hit = mg.stop_tier(tier, find=lambda marks: [4242],
                       signal_group=lambda pid: seen.append(pid),
                       signal_pid=lambda pid: seen.append(pid))
    check("stop_tier signals a widened tier tuple", (hit, seen), ([4242], [4242]))


def shed_compat(module, **kw):
    """Call shed() with trigger= if it takes one; on main (no trigger kwarg) call without it,
    which is the unguarded behaviour the test exists to catch."""
    import inspect
    params = inspect.signature(module.shed).parameters
    if "trigger" in params:
        return module.shed(**kw)
    kw.pop("trigger", None)
    return module.shed(**kw)


def test_pressure_shed_spares_tests():
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "guard.jsonl"
        signalled = []
        # Only a tests-tier process is running (the 42/42 shape: sweep up, no build running).
        def find(marks):
            return [777] if any("hourly_checks" in m for m in marks) else []
        def stop(tier, _f=find):
            return mg.stop_tier(tier, find=_f,
                                signal_group=lambda pid: signalled.append(pid),
                                signal_pid=lambda pid: signalled.append(pid))
        row = shed_compat(mg, avail=18.0, ledger=ledger, stop=stop, settle=0,
                          measure=lambda: 18.0, trigger="pressure",
                          why="pressure 90% (10 s) / 61% (60 s), shed at 75/60")
        check("pressure shed at 18 GB free does not kill the sweep", row, None)
        check("no signal reached the sweep", signalled, [])
        check("no ledger row for a shed that stopped nothing", ledger.exists(), False)


def test_reserve_shed_still_stops_tests():
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "guard.jsonl"
        signalled = []
        def find(marks):
            return [777] if any("hourly_checks" in m for m in marks) else []
        def stop(tier, _f=find):
            return mg.stop_tier(tier, find=_f,
                                signal_group=lambda pid: signalled.append(pid),
                                signal_pid=lambda pid: signalled.append(pid))
        row = mg.shed(1.5, ledger=ledger, stop=stop, settle=0, measure=lambda: 6.0)
        check("reserve path (1.5 GB) still stops the tests tier",
              (row["stopped"], row["pids"], signalled), ("tests", [777], [777]))
        check("the reserve shed records a row", len(ledger.read_text().splitlines()), 1)


def test_pressure_shed_still_stops_builds():
    with tempfile.TemporaryDirectory() as td:
        ledger = Path(td) / "guard.jsonl"
        signalled = []
        def find(marks):
            return [888] if any("coder.py" in m for m in marks) else []
        def stop(tier, _f=find):
            return mg.stop_tier(tier, find=_f,
                                signal_group=lambda pid: signalled.append(pid),
                                signal_pid=lambda pid: signalled.append(pid))
        row = mg.shed(18.0, ledger=ledger, stop=stop, settle=0, measure=lambda: 18.0,
                      why="pressure 90% (10 s) / 61% (60 s), shed at 75/60")
        check("pressure path (no trigger arg) keeps stopping builds",
              (row["stopped"], row["pids"], signalled), ("builds", [888], [888]))
        check("the pressure shed keeps its why", "pressure" in json.loads(ledger.read_text())["why"], True)


def test_run_wires_the_pressure_path():
    """run() may not run in a test; the wiring is checked in source (the same method as
    test_memory_guard_tiers.py uses for the tier order)."""
    src = (Path(__file__).resolve().parents[2] / "scripts" / "memory_guard.py").read_text(encoding="utf-8")
    body = src[src.index("def run()"):]
    check("the pressure branch says which trigger it is shedding on",
          'trigger="pressure"' in body, True)


test_tier_shape()
test_stop_tier_widened_tuple()
test_pressure_shed_spares_tests()
test_reserve_shed_still_stops_tests()
test_pressure_shed_still_stops_builds()
test_run_wires_the_pressure_path()

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
