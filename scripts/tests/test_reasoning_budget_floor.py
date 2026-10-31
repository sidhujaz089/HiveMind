#!/usr/bin/env python3
"""A profile with reasoning on leaves room to answer (2026-10-19).

Reasoning is billed to num_predict. The Supervisor ran with think on and num_predict 2000 on Swift:
reasoning took the budget, both t1309 decisions came back done_reason=length at 342 characters,
were recorded "unreadable", and the failure sat undecided (fixed to 16000 in e97d8155). This holds
every reasoning profile above a floor, so the next one cannot be set that low without a red suite.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import settings  # noqa: E402

FLOOR = 8000          # 4x the budget that starved the Supervisor; every reasoning profile today is >= 8192
failed = 0
for name, p in settings.load().items():
    if not isinstance(p, dict) or "num_predict" not in p:
        continue
    think = p.get("think")
    budget = p.get("num_predict")
    if think in (None, False, "off", "none") or budget in (None, -1):
        continue
    ok = budget >= FLOOR
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: think={think} num_predict={budget}"
          + ("" if ok else f" -- below the {FLOOR} floor; reasoning can eat the whole reply"))
print("FAILED" if failed else "ok")
sys.exit(1 if failed else 0)
