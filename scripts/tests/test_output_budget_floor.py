#!/usr/bin/env python3
"""Plan harness #6 (2026-10-19): a reasoning profile whose num_predict is under the floor for its
think level is named; off, unlimited (-1) and enough are not. Fixture settings only.

Run: python3 scripts/tests/test_output_budget_floor.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "projects" / "local-inference"))
import settings as S  # noqa: E402

bad = 0


def check(label, got, want):
    global bad
    bad += got != want
    print(f"  {'ok ' if got == want else 'BAD'} {label}: {got!r}")


f = S.budget_findings(settings={"supervisor": {"think": "low", "num_predict": 2000},
                                "coder": {"think": "high", "num_predict": 16000},
                                "fast": {"think": False, "num_predict": 100},
                                "open": {"think": "high", "num_predict": -1}})
check("the Supervisor's 2000 at low is named", len(f) == 1 and "supervisor" in f[0], True)
f = S.budget_findings(settings={"chat": {"think": "low", "num_predict": 4000}},
                      roles={"manager": {"attributes": {"think": "high", "num_predict": 8000}}})
check("a role's own override is checked against its level", [x.split(":")[0] for x in f], ["role 'manager' attributes"])
print(f"{'FAILED' if bad else 'ok'}")
sys.exit(1 if bad else 0)
