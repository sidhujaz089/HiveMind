#!/usr/bin/env python3
"""Run accounting: every walked item reaches exactly one terminal state, or the run is invalid.

On 2026-09-07 a 35-item measurement reported 33 rows for 35 items walked. `scope_queue.py`
does `except Exception: continue` and writes no row at all, so the run forgot two items it
had walked, and said nothing. It was noticed only because Jas remembered clicking a button.

A bad answer is visible. A missing record makes the whole dataset look healthier than it is
-- the denominator itself was wrong. All nine outside reviewers of draft four named this
independently, and it is the one thing all nine agreed on. Several said it must block any
further measurement, because a number from an unreconciled run is quarantined under this
repository's own rules.

The shape: take the manifest BEFORE the run, write a row on ENTRY rather than on success,
and reconcile at the end. `rows == walked` would have caught both errors draft four found
by hand, and neither would have needed a human to remember anything.

**The contract lives in `scripts/tests/test_runledger.py` and is enforced there, not here.**
"""
import json
import time
from pathlib import Path

TERMINAL = frozenset({
    "ok", "no-json", "invalid-json", "no-verdict", "ambiguous",
    "http_5xx", "timeout", "error",
})


class Ledger:
    def __init__(self, path, run_id, walked, collector_version=""):
        self.path = Path(path)
        self.run_id = run_id
        self.walked = list(walked)
        self.collector_version = collector_version

    def _write(self, row):
        row["run_id"] = self.run_id
        row["collector_version"] = self.collector_version
        row["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(self.path, "a") as f:
            f.write(json.dumps(row) + "\n")

    def start(self, item_id, **fields):
        self._write({"id": item_id, "status": "started", **fields})

    def finish(self, item_id, status, **fields):
        if status not in TERMINAL:
            raise ValueError(f"status {status!r} is not in TERMINAL")
        self._write({"id": item_id, "status": status, **fields})

    def mark_tainted(self, broken_by, reason):
        self._write({"id": "_taint", "status": "tainted", "broken_by": broken_by, "reason": reason})

    def reconcile(self):
        rows = []
        unreadable_lines = 0
        if self.path.exists():
            with open(self.path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            unreadable_lines += 1

        terminal_rows = {}
        for row in rows:
            # Legacy rows carry no run_id and still count; only a row stamped with a
            # DIFFERENT run_id belongs to another run and is skipped.
            if row.get("run_id") is not None and row["run_id"] != self.run_id:
                continue
            if row.get("status") in TERMINAL:
                if row.get("run_id") not in (None, self.run_id):
                    continue  # a different run's row; rows stamped before run_id existed have none and still count
                iid = row["id"]
                terminal_rows[iid] = terminal_rows.get(iid, 0) + 1

        terminal_ids = set(terminal_rows.keys())
        walked_set = set(self.walked)

        missing = [i for i in self.walked if i not in terminal_ids]
        extra = [i for i in terminal_ids if i not in walked_set]
        duplicated = [i for i, count in terminal_rows.items() if count > 1]

        result = {
            "ok": not missing and not extra and not duplicated,
            "walked": len(self.walked),
            "terminal": len(terminal_ids),
            "missing": missing,
            "extra": extra,
            "duplicated": duplicated,
            "unreadable_lines": unreadable_lines,
        }

        taint_rows = [r for r in rows if r.get("status") == "tainted" and r.get("run_id") == self.run_id]
        if taint_rows:
            t = taint_rows[-1]
            result["taint"] = True
            result["taint_reason"] = f"{t.get('broken_by', 'unknown')}: {t.get('reason', 'unknown')}"
            result["ok"] = False

        return result
