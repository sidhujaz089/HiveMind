#!/usr/bin/env python3
"""t4387: every big ledger is declared, and every declared ledger is inside its bound.

Measured 2026-10-25: workspace/run-logs/slot-ledger.jsonl is 3.91 GB and still growing,
memory-watch.jsonl 288 MB, dispatch-loop.jsonl 87 MB. Nothing said which of them may be pruned,
after how long, or with what command, and nothing noticed slot-ledger crossing 3.9 GB. A ledger that
grows without a declared bound is how a 2.71 GB file became a 3.91 GB file in the same week.

Three failures this oracle insists on, because each one is a way the check could read as assurance
while knowing nothing:
  1. a big ledger that is NOT in the table            -> a finding (a new ledger must not escape by being new)
  2. a declared ledger OVER its bound, not declared   -> a finding (the bound must mean something)
  3. a table that cannot be read                      -> a finding (absence is never compliance --
                                                         the same lesson as t1594's projection check)
and two that must NOT fire: a declared over-bound entry clears its ledger, and a declared ledger that
is no longer on disk is not a finding (a retired ledger is not a problem).

Hermetic: every file is under a TemporaryDirectory, and the declaration threshold is a parameter so
the fixtures can be 1 KB instead of 50 MB.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402

FAILS = []


def check(what, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond else "  -- " + str(detail)))
    if not cond:
        FAILS.append(what)


def main():
    with tempfile.TemporaryDirectory() as d:
        r = Path(d)
        ws = r / "workspace"
        (ws / "run-logs").mkdir(parents=True)
        table = ws / "ledger-retention.json"

        # 1. THE TABLE IS MISSING. Nothing may read as compliant.
        f = cw.ledger_retention_findings(r, min_declare_mb=0)
        check("a missing table is a finding, not silence",
              any("cannot be read" in x for x in f), f)

        big = ws / "a.jsonl"
        big.write_text("x" * 2000)
        table.write_text(json.dumps({"ledgers": {"workspace/a.jsonl": {
            "class": "reconstructible", "max_gb": 0.0000001,
            "prune": "rotate it", "why": "a fixture"}}}))
        f = cw.ledger_retention_findings(r, min_declare_mb=0)
        check("a declared ledger over its bound is a finding",
              any("over its" in x and "a.jsonl" in x for x in f), f)

        # 2. THE SAME LEDGER, NOW DECLARED OVER-BOUND, WITH THE ACTION NAMED.
        table.write_text(json.dumps({
            "ledgers": {"workspace/a.jsonl": {"class": "reconstructible", "max_gb": 0.0000001,
                                             "prune": "rotate it", "why": "a fixture"}},
            "_declared_over_bound": {"workspace/a.jsonl": "rotation of a live file; see t4387"}}))
        f = cw.ledger_retention_findings(r, min_declare_mb=0)
        check("a declared over-bound entry clears its ledger",
              not any("a.jsonl" in x for x in f), f)

        # 3. A LEDGER ABOVE THE THRESHOLD THAT NOBODY DECLARED.
        (ws / "b.jsonl").write_text("x" * 2000)
        f = cw.ledger_retention_findings(r, min_declare_mb=0)
        check("an undeclared ledger above the threshold is a finding",
              any("not in the retention table" in x and "b.jsonl" in x for x in f), f)
        check("  and the declared one is still not named", not any("a.jsonl" in x for x in f), f)

        # 4. A DECLARED LEDGER THAT IS NO LONGER ON DISK IS NOT A FINDING.
        d2 = json.loads(table.read_text())
        d2["ledgers"]["workspace/gone.jsonl"] = {"class": "reconstructible", "max_gb": 1,
                                                 "prune": "rm", "why": "retired"}
        table.write_text(json.dumps(d2))
        f = cw.ledger_retention_findings(r, min_declare_mb=0)
        check("a retired ledger that is gone is not a finding",
              not any("gone.jsonl" in x for x in f), f)

        # 4b. A LEDGER THAT CANNOT BE READ IS UNKNOWN, NOT COMPLIANT (a dangling symlink stats badly).
        link = ws / "c.jsonl"
        if not link.exists():
            link.symlink_to(ws / "does-not-exist")
        f = cw.ledger_retention_findings(r, min_declare_mb=0)
        check("a ledger that cannot be read is a finding, not a skip",
              any("could not be read" in x and "c.jsonl" in x for x in f), f)
        link.unlink()

        # 5. AN UNREADABLE TABLE.
        table.write_text("{not json")
        f = cw.ledger_retention_findings(r, min_declare_mb=0)
        check("an unparseable table is a finding",
              any("cannot be read" in x for x in f), f)

    print("\n  %d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
