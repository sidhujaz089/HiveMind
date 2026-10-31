#!/usr/bin/env python3
"""The contract for stamping historical rows with the collector that produced them.

GATE 4 of the six the outside reviewers set. `workspace/scoping-proposals.jsonl` holds 70 rows
from before Phase 0 and NOT ONE carries a `collector_version`, so `evidence.read_rows` refuses
them for having no provenance rather than for being known-bad. Those are different facts and
the record should say which.

Draft four's open item 4: "anything derived from the broken collector still sits in the record
as though it were sound." ChatGPT's rule, which this obeys: INVALID measurements must remain
ACCESSIBLE but must not be CONSUMABLE as evidence by automation. Deleting them would destroy
evidence, and destroying evidence is the defect being repaired.

THE TWO PROPERTIES THAT MATTER MOST are the ones that make it safe to run on a file you cannot
afford to lose: it NEVER overwrites a version already present, and it writes atomically, so a
crash midway leaves the original intact rather than half a file.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "stamp_legacy.py"
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def jsonl(rows):
    p = Path(tempfile.mkdtemp()) / "rows.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def run(path, version="pre-phase0-broken"):
    return subprocess.run([sys.executable, str(SCRIPT), str(path), version],
                          capture_output=True, text=True, timeout=120)


def rows(p):
    return [json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()]


if not SCRIPT.exists():
    print(f"  [BAD]  {SCRIPT.relative_to(ROOT)} does not exist")
    print("         Required: stamp_legacy.py <file.jsonl> <version>")
    print("         Adds collector_version to rows that lack one. Never overwrites one that")
    print("         is present. Writes atomically. Reports stamped and left-alone counts.")
    print("\n0 passed, 1 failed\n")
    sys.exit(1)

print("\nRows without a version get one:")
p = jsonl([{"id": "a"}, {"id": "b"}])
r = run(p)
check("it exits cleanly", r.returncode, 0)
got = rows(p)
check("both rows are stamped", [x.get("collector_version") for x in got],
      ["pre-phase0-broken"] * 2)
check("and nothing else about the row changed", got[0]["id"], "a")
check("it says how many it stamped", "2" in r.stdout, True)

print("\nA version ALREADY THERE is never overwritten -- this is the safety property:")
p = jsonl([{"id": "a", "collector_version": "phase0-2026-09-08"},
           {"id": "b"}])
r = run(p)
got = rows(p)
check("the existing version survives", got[0]["collector_version"], "phase0-2026-09-08")
check("and the bare row is stamped", got[1]["collector_version"], "pre-phase0-broken")
check("it reports what it left alone", "1" in r.stdout, True)

print("\nRunning it twice changes nothing the second time -- it is idempotent:")
p = jsonl([{"id": "a"}, {"id": "b"}])
run(p)
first = Path(p).read_text()
r = run(p)
check("the file is byte-identical after a second run", Path(p).read_text(), first)
check("and it says it stamped none", "0" in r.stdout, True)

print("\nThe row count is preserved -- stamping must not lose or duplicate anything:")
p = jsonl([{"id": str(i)} for i in range(70)])
run(p)
check("70 rows in, 70 rows out", len(rows(p)), 70)
check("and the ids are unchanged and in order",
      [x["id"] for x in rows(p)], [str(i) for i in range(70)])

print("\nA TORN LINE is not silently dropped -- the file may be someone's only evidence:")
p = jsonl([{"id": "a"}])
with open(p, "a") as f:
    f.write('{"id": "b", "colle')
r = run(p)
check("it refuses rather than rewriting a file it cannot fully read", r.returncode != 0, True)
check("and says so", "line" in (r.stdout + r.stderr).lower(), True)
check("and the original is untouched", '{"id": "b", "colle' in Path(p).read_text(), True)

print("\nIt writes ATOMICALLY, so a crash cannot leave half a file:")
src = SCRIPT.read_text()
check("it renames a temp file into place", ("os.replace" in src or "Path.replace" in src
                                            or ".replace(" in src), True)
check("and does not truncate the target in place",
      'open(path, "w")' in src or "open(path, 'w')" in src, False)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
