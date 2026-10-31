#!/usr/bin/env python3
"""lane_status.build_progress: the skipped side is kind-filtered.

t1467 (2026-10-18): the tick's `skipped` list held ('rca','already running'),
('rca','PARKED: ...'), ('rca','DELIVERED') -- and the report counted them as a
running build, a held build and a review build. The queued side was fixed on
2026-10-01 (e5f107c0) but the skipped projection (lane_status.py:266-267)
still drops s[0] (the kind). These cases pin the skipped side: a non-build
kind in `skipped` counts as nothing; a production kind still counts.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import lane_status as LS   # noqa: E402

FAILED = 0


def check(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: got {got!r}, want {want!r}")


def ledger(rows):
    f = Path(tempfile.mkdtemp()) / "dispatch-loop.jsonl"
    f.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return f


def main():
    # all-rca skipped: none of running/held/review should fire
    tick = {"event": "tick", "at": "2026-10-01T02:00:00Z", "queued": [],
            "skipped": [("rca", "already running"),
                        ("rca", "PARKED: awaiting review"),
                        ("rca", "DELIVERED")]}
    bp = LS.build_progress(ledger([tick]))
    check("a running rca is not a running build", bp["running"], 0)
    check("a parked rca is not a held build", bp["held"], 0)
    check("a delivered rca is not a review build", bp["review"], 0)
    # production skipped: must still count (guards against blanket drop)
    tick = {"event": "tick", "at": "2026-10-01T02:00:00Z", "queued": [],
            "skipped": [("production", "already running")]}
    bp = LS.build_progress(ledger([tick]))
    check("a running production build still counts as running", bp["running"], 1)
    print("test_lane_status_skipped_kind:", "FAILED" if FAILED else "ok")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
