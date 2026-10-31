#!/usr/bin/env python3
"""Phase 0b-1: the split shed must fire BEFORE the kill line on a fast collapse, and the tier file
must decide which container the guard kills.

THE COLLAPSE (06-prompts-final.md, L1): 16.7 -> 1.7 GB in ~35 s, ~0.43 GB/s. With the timers in
workspace/guard-tiers.json (pause 10, shed 8, kill 2, grace 30 s) the pause starts near t=15.6 s and
the grace ends near t=45.6 s -- eleven seconds after free memory crossed 2 GB. Shed never fires; the
model is the first thing to die, which is the order t1597 exists to prevent.

The simulation drives the guard's own decision (memory_guard.FallRate and memory_guard.split_shed_due)
at the guard's own tick, and checks the OLD rule (grace only) fails on the same series, so a pass is
evidence of the fix and not of a test that cannot fail.

THE CONTAINERS OVERWRITE. memory_guard.py read the model's container from the tier file and then,
three lines later, assigned CONTAINERS = ("swift",), so editing the file changed nothing. The second
half imports a COPY of the guard beside a tier file naming another container, in a subprocess.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import memory_guard as mg  # noqa: E402

PASSED = FAILED = 0


def check(name, ok, detail=""):
    global PASSED, FAILED
    PASSED += bool(ok)
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        {detail}"))


def collapse(start=16.7, end=1.7, seconds=35.0, tick=0.5):
    n = int(seconds / tick)
    return [(i * tick, start - (start - end) * i / n) for i in range(n + 1)]


def simulate(series, L, use_slope=True):
    """Return (t, avail) of the first shed, or None, ending at the first sample below kill."""
    fall = mg.FallRate()
    paused_at = None
    for t, avail in series:
        fall.add(t, avail)
        if avail < L["kill_gb"]:
            return None
        if avail < L["pause_gb"] and paused_at is None:
            paused_at = t
            continue
        if paused_at is not None:
            rate = fall.rate() if use_slope else 0.0
            if mg.split_shed_due(avail, t - paused_at, rate, L):
                return t, avail
    return None


def main():
    L = dict(mg.DEFAULT_LEVELS)
    L.update({k: float(v) for k, v in json.loads((REPO / "workspace" / "guard-tiers.json").read_text())
              ["levels"].items()})
    series = collapse(tick=L["interval_s"])

    old = simulate(series, L, use_slope=False)
    check("the OLD rule (grace only) never sheds on the collapse -- the bug is real", old is None, old)
    new = simulate(series, L)
    check("the slope-aware rule sheds before the kill line", new is not None, new)
    if new:
        check("  and at or below the shed line, not earlier", new[1] < L["shed_gb"], new)
        check("  with at least 5 s to spare before kill", (new[1] - L["kill_gb"]) / 0.43 >= 5, new)

    slow = [(t, 9.5 - 0.001 * t) for t in [i * 0.5 for i in range(0, 200)]]   # 9.5 -> 9.4 GB, flat
    hit = simulate(slow, {**L, "shed_gb": 9.6})
    check("a slow drift below the shed line waits out the grace before shedding",
          hit is not None and hit[0] >= L["grace_s"], hit)
    check("grace expiry alone still sheds (no fall at all)",
          mg.split_shed_due(7.0, L["grace_s"], 0.0, L))
    check("above the shed line nothing sheds, however fast the fall",
          not mg.split_shed_due(L["shed_gb"] + 0.1, 1.0, 5.0, L))
    f = mg.FallRate(window_s=5)
    for t, a in [(0, 10), (1, 9), (2, 8), (10, 8)]:
        f.add(t, a)
    check("FallRate forgets samples older than its window", f.rate() == 0.0, f.rate())
    f2 = mg.FallRate()
    f2.add(0, 5)
    f2.add(1, 6)
    check("rising memory is a zero fall rate, never negative", f2.rate() == 0.0, f2.rate())

    # THE CONTAINERS OVERWRITE
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "scripts").mkdir()
        (Path(d) / "workspace").mkdir()
        shutil.copy2(REPO / "scripts" / "memory_guard.py", Path(d) / "scripts")
        tiers = json.loads((REPO / "workspace" / "guard-tiers.json").read_text())
        for t in tiers["tiers"]:
            if t["signal"] == "trip":
                t["processes"] = ["other-model"]
        (Path(d) / "workspace" / "guard-tiers.json").write_text(json.dumps(tiers))
        p = subprocess.run([sys.executable, "-c", "import memory_guard as m; print(m.CONTAINERS)"],
                           cwd=Path(d) / "scripts", capture_output=True, text=True, timeout=60)
        check("a changed tier file changes the container the guard kills",
              p.stdout.strip() == "('other-model',)", p.stdout + p.stderr)
    check("the live tier file still names swift", mg.CONTAINERS == ("swift",), mg.CONTAINERS)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
