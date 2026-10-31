#!/usr/bin/env python3
"""Does the RUNNING bridge match the source on disk? Asks the endpoint, not the module.

    python3 scripts/check_deployed.py

WHY THIS EXISTS (Chat, 2026-08-25)
----------------------------------
On 2026-08-25 the search default was fixed in `scripts/workspace_mcp/server.py`, covered by
a fixture, covered by a mutation, committed -- and reported to Chat as done. Chat re-ran the
identical call and got the identical broken result, because **the systemd service had been
running since 17:04 the previous day and nothing restarted it.** The fix was on disk and not
in the process.

Every check in this repository reads files. `mutate.py` breaks a function and requires a
suite to notice. None of that can see a stale process, because they all examine the same
bytes the deployment failed to pick up.

Chat's words, and they are the design of this file:

> A mutation asserting against the fixed function proves the function. It does not prove the
> bytes reaching claude.ai. The thing that has never been observed failing here is the
> DEPLOYMENT PATH, not the code.

WHAT IT ACTUALLY COMPARES
-------------------------
The tool schema the live endpoint advertises, against the signatures in the source. If they
disagree, the service is stale and everything else in this repo will cheerfully report PASS
while a user of the bridge sees the old behaviour.

This is the fourth blind spot in `docs/CHECKS.md`'s list -- "anything only observable from a
client" -- and it is the first check pointed at it. Note the shape of the original failure:
**it was true from inside the machine and false from outside it, and nothing here noticed.**
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pysource  # noqa: E402

UNPARSED = []

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "scripts" / "workspace_mcp" / "server.py"
CORE = REPO / "scripts" / "workspace_mcp" / "core.py"
ENDPOINT = "http://127.0.0.1:8787/mcp"
TOKEN_FILE = Path.home() / ".secrets" / "workspace-mcp-token"


def source_defaults():
    """Every tool's declared default in the source, as {tool: {arg: default}}.

    This was 20 lines of regex and a hand-rolled balanced-paren counter, arrived at over
    three agent tasks (t147, t148, t149). Every one of those patches was competent and every
    one was unnecessary -- `ast` parses Python correctly and always has. I framed each task
    as "fix the regex" and never asked why we were parsing Python with one.
    """
    got, unparsed = pysource.defaults(SERVER)
    UNPARSED[:] = unparsed
    return got


def declared_tools():
    """The tools the source says the HTTP bridge serves.

    `source_defaults()` returns only tools that HAVE a default argument -- three of the
    ten the server declares. Comparing the roster against THAT set meant seven tools were
    invisible to the "declared but not served" arm, which is the arm that catches a
    forgotten restart. `read` could have stopped being served and this file would still
    have printed PASS.

    Found on 2026-08-26 while re-verifying the round-71 fix, which closed the same hole
    for the three tools that happened to carry defaults. HTTP_TOOLS is the roster the
    server actually iterates, so it is the honest source of truth, and comparing against
    it also retires the hand-maintained `known_runtime` allowlist that only existed
    because `src` was defaults-only.
    """
    got = pysource.constant(CORE, "HTTP_TOOLS")
    return set(got or ())


def served_schema():
    if not TOKEN_FILE.is_file():
        raise RuntimeError(f"no token at {TOKEN_FILE}")
    tok = TOKEN_FILE.read_text().strip().split("\n")[0]
    head = {"Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {tok}"}

    def post(payload, sid=None):
        h = dict(head)
        if sid:
            h["mcp-session-id"] = sid
        r = urllib.request.Request(ENDPOINT, json.dumps(payload).encode(), h)
        with urllib.request.urlopen(r, timeout=30) as f:
            return f.headers.get("mcp-session-id"), f.read().decode()

    sid, _ = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params":
                   {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "check_deployed", "version": "1"}}})
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
    _, body = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, sid)
    m = re.search(r'\{"jsonrpc".*"tools".*', body)
    if not m:
        raise RuntimeError("no tools/list result from the endpoint")
    tools = json.loads(m.group(0).split("\n")[0])["result"]["tools"]
    out = {}
    for t in tools:
        props = (t.get("inputSchema") or {}).get("properties") or {}
        out[t["name"]] = {k: v.get("default") for k, v in props.items()
                          if "default" in v}
    return out


def main():
    print("\nDeployed bridge vs source on disk\n")
    try:
        served = served_schema()
    except Exception as e:
        # NOT a pass. A check that cannot reach the thing it checks has established
        # nothing, and saying so is the whole point of this file.
        print(f"  [SKIP] could not reach the bridge: {type(e).__name__}: {e}")
        print("         Nothing was checked -- this is not a pass.")
        return 2

    src = source_defaults()
    declared = declared_tools()
    bad = []
    checked = 0

    # A tool the source declares and the endpoint does not serve was SKIPPED here, silently,
    # by a `continue`. That is precisely the failure this file exists to catch: a change on
    # disk that never reached the process. Adding `recall` and `why` on 2026-08-26 and
    # forgetting to restart would have produced PASS.
    #
    # Found by the env3 probe at round 71, in a checker whose own docstring describes this
    # exact incident from 2026-08-25.
    for tool in sorted(declared - set(served)):
        bad.append(f"{tool}: declared in source, NOT served by the running bridge")

    # And the reverse: a process still advertising a tool the source has removed is the same
    # staleness pointing the other way, and equally invisible before now.
    for tool in sorted(set(served) - declared):
        bad.append(f"{tool}: served by the running bridge, NOT declared in source")

    for tool, defaults in src.items():
        if tool not in served:
            continue
        for arg, want in defaults.items():
            got = served[tool].get(arg)
            checked += 1
            if got != want:
                bad.append(f"{tool}({arg}=): source {want!r}, SERVED {got!r}")

    for b in bad:
        print(f"  [FAIL] {b}")
    if bad:
        print("\n  The running service does not match the source. Restart it:")
        print("      systemctl --user restart workspace-mcp")
        print("  Until then every file-reading check here will pass while the bridge")
        print("  serves the old behaviour -- which is how this was found (Chat, 2026-08-25).")
        return 1

    for u in UNPARSED:
        print(f"  [WARN] {u}: could not read from source, NOT compared")
    # "checked == 0 AND UNPARSED" only caught "I TRIED to parse and could not". It missed
    # "there was NOTHING to parse" -- an empty or moved server.py gives got={} and
    # unparsed=[], so this fell through to [PASS] having examined nothing. SERVER is a
    # hardcoded path; any rename makes that pass silently and forever, and a passing check
    # is never investigated. Zero examined is not a pass, whatever the reason.
    if checked == 0:
        why = ("could not be read from source" if UNPARSED
               else f"none were found in {SERVER.name} at all -- has it moved or been emptied?")
        print(f"  [FAIL] no defaults were comparable -- nothing was examined: {why}")
        return 1

    # Report the ROSTER size, not len(src). Printing the defaults-only count said
    # "3 checked" while ten tools existed, which is how the gap stayed invisible.
    print(f"  [PASS] every tool the source declares is served ({len(declared)} in the roster)")
    print(f"  [PASS] {checked} tool default(s) match between source and the live endpoint")
    print(f"  [PASS] {len(served)} tool(s) advertised")
    return 0


if __name__ == "__main__":
    sys.exit(main())
