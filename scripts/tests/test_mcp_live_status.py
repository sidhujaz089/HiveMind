#!/usr/bin/env python3
"""The workspace MCP serves live_status to Chat: lane_status's report, read-only."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from workspace_mcp.core import Workspace, HTTP_TOOLS, STDIO_TOOLS  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    report("Chat's tunnelled server offers live_status", "live_status" in HTTP_TOOLS, True)
    report("  and so does stdio", "live_status" in STDIO_TOOLS, True)
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(ROOT, caller="test", log_path=Path(tmp) / "calls.jsonl")
        r = ws.live_status()
    report("it returns lane_status's report", "LANE STATUS" in r["content"], True)
    report("  and the build count", isinstance(r["builds_today"], (dict, type(None))), True)
    src = (ROOT / "scripts" / "workspace_mcp" / "server.py").read_text()
    report("the server registers the tool", "def live_status() -> dict:" in src, True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
