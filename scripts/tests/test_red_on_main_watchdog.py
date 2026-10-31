#!/usr/bin/env python3
"""GATE: Jas is told once when "red on main" starts holding builds and once when it clears
(2026-10-12; twice the hold stopped the line for most of a day, found by reading the Hub)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import dispatch_loop as d

sent = []
st = Path(tempfile.mkdtemp()) / "s.json"
held = [("p1", "HELD: red on main, so every sandbox baseline would refuse -- fix it first: test_a.py (refused p9)")]
send = lambda *a, **k: sent.append(a[1])
r1 = d.red_watchdog(held, send=send, state=st)
d.red_watchdog(held, send=send, state=st)
d.red_watchdog([("p2", "waiting -- every model has a lane")], send=send, state=st)
ok = [r1 == ["test_a.py"],
      sent == ["Builds held: a test is red on main", "Main is green again"],
      "red_watchdog(skipped)" in Path(d.__file__).read_text()]
for name, good in zip(["names the red suite", "one alert per change, not per tick", "the tick calls it"], ok):
    print(f"  {'PASS' if good else 'FAIL'}  {name}")
print(f"\n{sum(ok)} passed, {len(ok) - sum(ok)} failed")
sys.exit(0 if all(ok) else 1)
