#!/usr/bin/env python3
"""Contract for t313: kind comes from the row; the CLI prints findings.

Defect 1 (scripts/error_scan.py:264-272): the generic status rule reports ANY
non-HEALTHY status as kind 'backup-failed', so an ollama eviction
(status 'evicted', unit 'ollama'; scripts/ollama_events.py:31-38) reads as a
backup failure. The kind must come from the row: 'status-<the row's status>'.

Defect 2: 'python3 scripts/error_scan.py --all' / '--json' exit 0 printing
nothing -- the file ends at line 306 with no main, no argparse, no sys import.

Written BEFORE the implementation; RED today for exactly those two reasons.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import error_scan                                            # noqa: E402

SCRIPT = REPO / "scripts" / "error_scan.py"
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    ok = got == want
    if ok:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def kinds(findings):
    return sorted({f["kind"] for f in findings})


def _json(out):
    try:
        v = json.loads(out)
    except ValueError as e:
        print(f"    (stdout was not JSON: {e}; got {out!r})")
        return None
    return v if isinstance(v, dict) else None


d = Path(tempfile.mkdtemp())


def runs(*rows):
    p = d / f"runs{len(list(d.iterdir()))}.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return p


def cli(*args):
    r = subprocess.run([sys.executable, str(SCRIPT), *[str(a) for a in args]],
                       capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout, r.stderr


print("A FAILURE KIND MUST COME FROM THE ROW. An eviction is not a backup failure:")
f = error_scan.scan([runs({"at": "e1", "unit": "ollama", "status": "evicted",
                           "message": "llama-server model predicted to exceed available memory, evicting"})])
check("one finding", len(f), 1)
check("kind is the row's status, not a backup label", kinds(f), ["status-evicted"])

f = error_scan.scan([runs({"at": "o1", "unit": "ollama", "status": "out-of-memory",
                           "message": "runner out of memory"})])
check("an OOM event reads as an OOM event", kinds(f), ["status-out-of-memory"])

f = error_scan.scan([runs({"at": "b1", "job": "nas-backup", "status": "verify-failed",
                           "detail": "does NOT read back byte-identical"})])
check("a failed backup still surfaces, with its own status as the kind", kinds(f), ["status-verify-failed"])

f = error_scan.scan([runs({"at": "u1", "job": "x", "status": "quantum-flux"})])
check("an UNRECOGNISED status still surfaces (enumerate the good)", kinds(f), ["status-quantum-flux"])

f = error_scan.scan([runs({"at": "h1", "job": "nas-backup", "status": "verified"})])
check("a healthy row still produces nothing", f, [])

print("\nTHE CLI MUST PRINT ITS FINDINGS. Today the script has no main:")
bad = runs({"at": "c1", "unit": "ollama", "status": "evicted", "message": "evicting now"})
rc, out, err = cli("--all", str(bad))
check("it exits 0", rc, 0)
check("and it prints the finding's kind", "status-evicted" in out, True)
check("and it names the source", bad.name in out, True)

print("\n--json IS THE MACHINE FORM the docstring promised 'for check_workspace':")
rc, out, err = cli("--json", str(bad))
check("it exits 0", rc, 0)
doc = _json(out) or {}
check("the stdout is a JSON object with both keys", isinstance(doc, dict) and "findings" in doc and "skipped" in doc, True)
check("one finding", len(doc.get("findings", [])), 1)
check("with the whole evidence", (doc.get("findings") or [{}])[0].get("evidence"),
      json.dumps({"at": "c1", "unit": "ollama", "status": "evicted", "message": "evicting now"}))
check("and empty skipped for a readable file", doc.get("skipped"), [])

print("\nA MISSING SOURCE IS STILL DISTINCT (L-146), now visible in the CLI:")
missing = d / "nope.jsonl"
rc, out, err = cli("--json", str(missing))
check("it exits 0", rc, 0)
doc = _json(out) or {}
check("no findings", doc.get("findings"), [])
check("and the skip is named", doc.get("skipped"), [str(missing)])

print("\nA CLEAN FILE SAYS IT IS CLEAN (zero findings, no crash):")
clean = d / "clean.jsonl"
clean.write_text(json.dumps({"at": "k1", "status": "verified"}) + "\n")
rc, out, err = cli("--json", str(clean))
check("it exits 0", rc, 0)
doc = _json(out) or {}
check("zero findings", doc.get("findings"), [])
check("no skips", doc.get("skipped"), [])

print(f"\n  {PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
