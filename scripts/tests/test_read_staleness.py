#!/usr/bin/env python3
"""Fixture for the read-staleness check (G4/G5, Chat's C4).

The check compares the sha256 the SERVER recorded when it served a file against what the file
is now. Two independent records, neither of them Chat's account of itself.

The assertions that matter are the quiet ones: it must not flag an unchanged file, and it must
not flag an OLD read of a file that was read again more recently. Both would make it noise,
and noise is how a check stops being read.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "check_read_staleness.py"
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def run(files, rows, *args):
    """(exit, output) from the real script against a synthetic repo and log."""
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "workspace").mkdir(parents=True)
        for name, body in files.items():
            f = tmp / name
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body)
        (tmp / "workspace" / "mcp-calls.jsonl").write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n")
        src = SCRIPT.read_text().replace(
            "REPO = Path(__file__).resolve().parent.parent", f"REPO = Path({str(tmp)!r})")
        shim = tmp / "under_test.py"
        shim.write_text(src)
        r = subprocess.run([sys.executable, str(shim), "--strict", *args],
                           capture_output=True, text=True)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def read_row(ts, target, digest):
    return {"ts": ts, "tool": "read", "outcome": "ok", "target": target, "sha256": digest}


def main():
    print("A file that changed since it was read:")
    rc, out = run({"a.txt": "NEW"},
                  [read_row("2026-08-22T10:00:00Z", "a.txt", sha("OLD"))])
    report("flagged", rc, 1)
    report("named as CHANGED", "CHANGED" in out, True)

    print("\nA file that has not changed:")
    rc, out = run({"a.txt": "SAME"},
                  [read_row("2026-08-22T10:00:00Z", "a.txt", sha("SAME"))])
    report("not flagged", rc, 0)

    print("\nA file read twice: only the MOST RECENT read counts:")
    rc, out = run({"a.txt": "V2"},
                  [read_row("2026-08-22T10:00:00Z", "a.txt", sha("V1")),
                   read_row("2026-08-22T11:00:00Z", "a.txt", sha("V2"))])
    report("the stale older read is not noise", rc, 0)

    print("\nA file that has since been deleted:")
    rc, out = run({}, [read_row("2026-08-22T10:00:00Z", "gone.txt", sha("X"))])
    report("flagged as missing", rc, 1)
    report("named as MISSING", "MISSING" in out, True)

    print("\nRows that must be ignored:")
    rc, _ = run({"a.txt": "NEW"},
                [{"ts": "2026-08-22T10:00:00Z", "tool": "read", "outcome": "refused",
                  "target": "a.txt", "sha256": sha("OLD")}])
    report("a refused read", rc, 0)
    rc, _ = run({"a.txt": "NEW"},
                [{"ts": "2026-08-22T10:00:00Z", "tool": "search", "outcome": "ok",
                  "target": "a.txt", "sha256": sha("OLD")}])
    report("a non-read tool", rc, 0)

    print("\n--since excludes older reads:")
    rc, _ = run({"a.txt": "NEW"},
                [read_row("2026-08-19T10:00:00Z", "a.txt", sha("OLD"))], "--since", "2026-08-21")
    report("an old read is out of scope", rc, 0)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
