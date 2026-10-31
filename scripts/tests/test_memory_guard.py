#!/usr/bin/env python3
"""GATE: memory_guard.py keeps a reserve -- below it, Swift is killed and the key turned off
(Jas, 2026-10-09: "A couple of GB in reserve so I can send terminal commands from my phone").
Hermetic: fake meminfo, fake kill, fake breaker, a temp ledger. Nothing real is killed."""
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


with tempfile.TemporaryDirectory() as td:
    mi = Path(td) / "meminfo"
    mi.write_text("MemTotal:       127535680 kB\nMemAvailable:     1048576 kB\n")
    check("reads MemAvailable in GB", mi.exists() and round(mg.available_gb(str(mi)), 2), 1.0)
    killed, keys = [], []
    ledger = Path(td) / "guard.jsonl"
    row = mg.trip(1.0, kill=lambda n: killed.append(n) or True,
                  breaker=lambda who, why: keys.append((who, why)) or (True, ""),
                  ledger=ledger, names=("swift",))
    check("Swift is killed", killed, ["swift"])
    check("the master key is turned off, by the guard", [k[0] for k in keys], ["memory-guard"])
    check("the reason names the reserve", str(mg.RESERVE_GB) in keys[0][1], True)
    check("one ledger row", len(ledger.read_text().splitlines()), 1)
    check("the row records what was killed", json.loads(ledger.read_text())["killed"], ["swift"])
    check("the reserve is at least the couple of GB Jas asked for", mg.RESERVE_GB >= 2, True)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
