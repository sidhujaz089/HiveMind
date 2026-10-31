#!/usr/bin/env python3
"""Oracle for the INVERTED truncation detector (t297-B1).

The old gate (scripts/signature_gate.py) is a BLACKLIST OF SYNTAXES: it asks
"does this line slice a record?" and misses f-strings, .format, removesuffix,
non-Python, and whatever syntax comes next.  t297 inverts: measure the ARTIFACT.
A recorded value cut to one fixed width leaves a MODE in the per-field length
distribution that natural text does not have, regardless of the syntax that cut
it.  This file pins both sides of that claim:

  * 20 rows whose 'evidence' is ALL exactly 400  -> must be FLAGGED
  * 20 rows with naturally varying lengths (incl. ONE 400) -> must be SILENT
  * 10 of 20 exactly 400 (the pinned boundary)   -> must be FLAGGED

Red today because scripts/error_scan.py has no 'record-suspiciously-exact' kind
and no per-file length histogram (I read all 306 lines; KINDS at :87-95 has no
such entry).
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import error_scan  # noqa: E402

PASSED = 0

def check(label, got, want):
    global PASSED
    ok = (got == want)
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}")
    if not ok:
        print(f"         got : {got!r}")
        print(f"         want: {want!r}")
        raise SystemExit(1)
    PASSED += 1

def runs(*rows) -> Path:
    d = Path(tempfile.mkdtemp())
    p = d / "ledger.jsonl"
    with p.open("w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return p

def rows_with(evidence_lens):
    out = []
    for i, n in enumerate(evidence_lens, 1):
        out.append({"at": f"r{i:02d}", "job": "collect-logs",
                    "status": "collected", "evidence": "x" * n})
    return out

def exact_findings(findings):
    return [x for x in findings if x.get("kind") == "record-suspiciously-exact"]

print("t297-B1 · inverted detector: flag the CUT, stay silent on the NATURAL")

cut = runs(*rows_with([400] * 20))
f = error_scan.scan([str(cut)])
ex = exact_findings(f)
check("a field cut to one width IS flagged", len(ex) >= 1, True)
blob = json.dumps(ex[0]) if ex else ""
check("evidence names the field 'evidence'", "evidence" in blob, True)
check("evidence names the width 400", "400" in blob, True)
check("evidence names a row id (locator, not just a count)",
      ("r0" in blob) or ("r1" in blob) or ("locator" in blob) or ("id" in blob), True)

natural = [12, 37, 55, 89, 134, 201, 260, 312, 350, 399,
           400, 401, 433, 505, 612, 701, 748, 820, 903, 1204]
n = error_scan.scan([str(runs(*rows_with(natural)))])
check("a naturally varying field (incl one 400) is SILENT",
      exact_findings(n), [])

half = runs(*rows_with([400] * 10 + [13, 91, 210, 333, 540, 661, 772, 883, 995, 1104]))
check("10 of 20 at exactly 400 IS flagged (pinned boundary)",
      len(exact_findings(error_scan.scan([str(half)]))) >= 1, True)

print(f"\n  {PASSED} checks passed")
