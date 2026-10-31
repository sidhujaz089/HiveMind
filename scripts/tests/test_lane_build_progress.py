#!/usr/bin/env python3
"""lane_status's BUILDS TODAY line: done/total and an ETA from the measured median build.

Fixture ledger only. Held/parked chunks are not in the total (they wait on a person).
"""
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import lane_status as LS  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def at(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def main():
    now = time.time()
    day0 = now - (now % 86400) + 3600          # 01:00 UTC today
    rows = []
    for i, (s, e, rc) in enumerate([(day0, day0 + 600, 0), (day0 + 700, day0 + 1900, 1),
                                    (day0 - 86400, day0 - 86400 + 900, 0)]):
        rows += [{"event": "started", "kind": "production", "pid": i, "at": at(s)},
                 {"event": "finished", "kind": "production", "pid": i, "at": at(e), "exit": rc}]
    rows.append({"event": "started", "kind": "production", "pid": 9, "at": at(now - 300)})
    rows.append({"event": "tick", "queued": [], "skipped": [
        ["p1", "already running"], ["p2", "waiting -- every model has a lane"],
        ["p3", "waiting -- every model has a lane"], ["p4", "HELD FOR CLAUDE: x"],
        ["p5", "PARKED: 1 attempt"], ["p6", "DELIVERED, waiting on Claude's review: b"]]})
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "d.jsonl"
        f.write_text("\n".join(json.dumps(r) for r in rows))
        bp = LS.build_progress(f, now=now, lanes=2)
    report("done counts today's finished builds only", bp["done"], 2)
    report("  and how many were accepted", bp["accepted"], 1)
    report("running and waiting come from the latest tick", (bp["running"], bp["waiting"]), (1, 2))
    report("total = done + running + waiting", bp["total"], 5)
    report("held/parked and delivered are reported, not totalled", (bp["held"], bp["review"]), (2, 1))
    report("median of the three measured builds (600, 900, 1200 s)", bp["median_s"], 900)
    report("ETA = running remainder (900-300) + 2 waiting / 2 lanes x 900 (whole-second stamps)",
           abs(bp["eta_s"] - 1500) <= 2, True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
