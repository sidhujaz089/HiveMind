#!/usr/bin/env python3
"""t523: every lease acquire, refusal and release leaves a row saying who, which model, for what."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import modellease as M  # noqa: E402

d = Path(tempfile.mkdtemp())
M.LEDGER = d / "lease-ledger.jsonl"
lp = d / "lease.json"
a = M.acquire(lp, "m", "scoping t1", 60, holder="job-A")
try:
    M.acquire(lp, "m", "review t2", 60, holder="job-B")
except M.Held:
    pass
M.release(lp, a)
rows = [json.loads(l) for l in M.LEDGER.read_text().splitlines()]
got = [(r["event"], r.get("holder"), r.get("held_by")) for r in rows]
want = [("acquired", "job-A", None), ("refused", "job-B", "job-A"), ("released", "job-A", None)]
print("test_lease_ledger:", "ok" if got == want else f"FAILED {got}")
sys.exit(0 if got == want else 1)
