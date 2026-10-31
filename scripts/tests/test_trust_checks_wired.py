#!/usr/bin/env python3
"""The two trust checks the t105 audit found built and never run are now called by
check_workspace (t487): a failed ledger load read from the SERVER's call log, and Chat sessions
with nothing captured. This proves the ledger half both ways on a fixture, and that the Chat
bridge section calls the helper."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_workspace as cw  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def run(rows):
    tmp = Path(tempfile.mkdtemp(prefix="trust-wired-"))
    try:
        (tmp / "workspace").mkdir()
        (tmp / "workspace" / "mcp-calls.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        c = cw.Checker(tmp, quick=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            c._check_server_side_trust()
        return out.getvalue()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def status(out):
    line = next(l for l in out.splitlines() if "No failed ledger load" in l)
    return next(t for t in ("PASS", "WARN", "FAIL", "SKIP") if f"[{t}]" in line)


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    target = "chat/projects/family/State_Ledger_Family_Project.txt"
    bad = run([{"ts": now, "tool": "read", "target": target, "outcome": "refused", "detail": "denied"}])
    report("a refused ledger read in the server log -> WARN, without the session saying so",
           status(bad), "WARN")
    report("  and it shows the FAILED-LOAD line", "FAILED-LOAD" in bad, True)
    good = run([{"ts": now, "tool": "read", "target": target, "outcome": "ok", "bytes": 10}])
    report("a clean load -> PASS", status(good), "PASS")
    src = (ROOT / "scripts" / "check_workspace.py").read_text()
    report("the Chat bridge section calls it (run by session start and end)",
           'self.section("Chat bridge")\n        self._check_server_side_trust()' in src, True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
