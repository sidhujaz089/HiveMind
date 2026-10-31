#!/usr/bin/env python3
"""The app pipeline and the corpus digests get a home, so a close-out can stop doing them (2026-10-30).

Jas: "I want to only do what's necessary and move other routines out of session end where possible." The corpus
already had `corpus-index.timer`; the app pipeline (apksigner, emulated here) and the watched-corpus digests had
nothing, so they stayed in the close-out by default -- a 600 s ceiling each, paid on every close.

This suite pins the runner that becomes their home:
  * a check that fails is RECORDED, not thrown away (a scheduled job that dies silently is the defect class);
  * a check that times out is recorded as a timeout, and the other check still runs;
  * one row per check per run, with its exit code and duration, in a ledger someone can read back.

RED ON MAIN: scripts/slow_checks.py does not exist --
  ModuleNotFoundError: No module named 'slow_checks'
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import slow_checks as sc          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


with tempfile.TemporaryDirectory() as td:
    ledger = Path(td) / "slow-checks.jsonl"

    def green():
        return subprocess.CompletedProcess([], 0, "", "")

    def red():
        return subprocess.CompletedProcess([], 2, "apksigner disagrees", "")

    rows = sc.run_all(ledger, checks=[("apps", green), ("digests", green)])
    got = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("one row per check", len(got) == 2, str(len(got)))
    check("a row names the check and its exit code",
          [(r["check"], r["rc"]) for r in got] == [("apps", 0), ("digests", 0)], str(got))
    check("a row carries a duration", all(isinstance(r.get("seconds"), (int, float)) for r in got), str(got))
    check("the summary says both passed", rows["failed"] == 0, str(rows))

    sc.run_all(ledger, checks=[("apps", red), ("digests", green)])
    got = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("a FAILING check is still recorded", got[-2]["check"] == "apps" and got[-2]["rc"] == 2, str(got[-2]))
    check("  and its output is kept, not dropped", "apksigner" in (got[-2].get("tail") or ""), str(got[-2]))
    check("  and the run reports one failure", sc.run_all(ledger, checks=[("apps", red)])["failed"] == 1)

    def boom():
        raise subprocess.TimeoutExpired(cmd="apps", timeout=5)

    rows = sc.run_all(ledger, checks=[("apps", boom), ("digests", green)])
    got = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    check("a timeout is recorded, not raised", got[-2]["rc"] != 0 and "timeout" in (got[-2].get("tail") or "").lower(),
          str(got[-2]))
    check("  and the OTHER check still ran", got[-1]["check"] == "digests", str(got[-1]))
    check("  and a timeout counts as a failure", rows["failed"] == 1, str(rows))

print("test_slow_checks:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
