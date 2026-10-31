#!/usr/bin/env python3
"""t682 fail-first oracle: the three task-write tools are REGISTERED by the MCP server.

Red on main: scripts/workspace_mcp/server.py defines task_get, task_note and task_assign,
and no task_create, task_cancel or task_done; the --tools filter rejects any name outside
STDIO_TOOLS, and the served registry is built from STDIO_TOOLS alone.

This suite reads server.py as TEXT and never imports it. Importing runs the MCP SDK's
decorator machinery at module scope (@app.tool on the tool functions), so an importing oracle
would depend on a package the builder's unattended run may not have -- and an oracle that
crashes proves nothing (see scope-quality class oracle-crash). Text assertions pin the three
things a person checking by hand checks: the tool is defined, it forwards to the Workspace
method (so the write goes through taskstore and self.log, not a second implementation in the
server), and the http-only names are not filtered out of the set the transport is built from.
"""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "scripts" / "workspace_mcp" / "server.py"
PASSED = 0
FAILED = 0
WINDOW = 1500


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def main():
    print("\nworkspace_mcp: task_create / task_cancel / task_done registered over HTTP (t682)\n")
    text = SRC.read_text(encoding="utf-8")
    for name in ("task_create", "task_cancel", "task_done"):
        check(f"server.py defines {name}", f"def {name}(" in text, True)
        check(f"  and {name} forwards to the Workspace method ws.{name}",
              f"ws.{name}" in text, True)
    at = text.find("def task_create(")
    check("task_create's description states the 80-character title limit",
          ("80" in text[at:at + WINDOW]) if at >= 0 else False, True)
    check("a tool that is HTTP-only is not rejected by the --tools filter as unknown",
          "if t not in STDIO_TOOLS]" in text, False)
    check("the served registry is not built from STDIO_TOOLS alone",
          "for t in STDIO_TOOLS if t not in removed" in text, False)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return FAILED


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
