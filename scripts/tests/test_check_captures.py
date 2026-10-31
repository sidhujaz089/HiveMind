#!/usr/bin/env python3
"""Fixture for the capture-gap check (L-086).

The check reports zero gaps against today's real log, and that is worth nothing on its own --
the first version reported FIVE, all of them windows from before the append tool existed. So
the assertions here are about what it must catch and what it must NOT.

The false-positive cases matter as much as the true one. A check that flags every quiet
window gets ignored, and an ignored check is worse than no check because it looks like
coverage.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "check_captures.py"
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def call(ts, tool, via="oauth:test"):
    return json.dumps({"ts": ts, "tool": tool, "via": via, "outcome": "ok"})


def verdict(calls, captures=()):
    """(exit_code, output) from the real script against a synthetic log."""
    tmp = Path(tempfile.mkdtemp())
    try:
        (tmp / "workspace").mkdir(parents=True)
        (tmp / "chat" / "captures").mkdir(parents=True)
        (tmp / "workspace" / "mcp-calls.jsonl").write_text("\n".join(calls) + "\n")
        for name in captures:
            (tmp / "chat" / "captures" / name).write_text("{}\n")
        src = SCRIPT.read_text().replace(
            "REPO = Path(__file__).resolve().parent.parent", f"REPO = Path({str(tmp)!r})")
        shim = tmp / "under_test.py"
        shim.write_text(src)
        r = subprocess.run([sys.executable, str(shim), "--strict"],
                           capture_output=True, text=True)
        return r.returncode, r.stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    busy = [call(f"2026-08-22T10:0{i}:00Z", "search") for i in range(6)]

    print("A busy session with no appends, AFTER append existed, is a gap:")
    rc, out = verdict(busy)
    report("flagged", rc, 1)
    report("and it says so", "uncaptured session" in out, True)

    print("\nThe same session WITH appends is not:")
    rc, out = verdict(busy + [call("2026-08-22T10:03:00Z", "append")])
    report("not flagged", rc, 0)

    print("\nFalse positives it must NOT raise:")
    rc, _ = verdict([call(f"2026-08-19T10:0{i}:00Z", "search") for i in range(6)])
    report("a busy session BEFORE append existed", rc, 0)

    rc, _ = verdict([call("2026-08-22T10:00:00Z", "read"),
                     call("2026-08-22T10:01:00Z", "read")])
    report("two calls is a lookup, not a session", rc, 0)

    rc, _ = verdict(busy, captures=("2026-08-22__something.jsonl",))
    report("no appends but a capture file exists that day", rc, 0)

    print("\nCode's own calls are not Chat's:")
    rc, _ = verdict([call(f"2026-08-22T10:0{i}:00Z", "search", via="direct")
                     for i in range(6)])
    report("via=direct is ignored", rc, 0)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
