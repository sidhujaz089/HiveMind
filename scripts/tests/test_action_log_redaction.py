#!/usr/bin/env python3
"""Fail-first oracle for t1563: the action log must not be able to carry a key shape.

WHY. `workspace/manager-actions.jsonl` records every command an agent runs, is TRACKED, and is
served to Chat. On 2026-10-24 eleven dispatches were refused and two checks were red because a
redaction TEST had put key-shaped strings into that log -- and there is no legitimate edit that
clears a tracked file whose history is append-only. The fix is at the writer: a string that
looks like a credential never reaches the log, so the scanners can keep zero path exclusions
and stay honest.

THE RAW VALUE IS NOT DISCARDED (Jas, 2026-10-24: "keep the raw text in the method you proposed
with the sensitivity labels"). Every match goes, whole, to a sensitivity-tagged record in
`workspace/private/secret-hits.jsonl` -- gitignored, and outside what the MCP server serves --
next to its sha256 and length, so the log keeps the identity of what it stopped and the
decision can still be made about it. That is the design this asserts.

Behaviour, not shape: what ends up in the log, what ends up in the private ledger, and that a
clean command is untouched. Both paths are pointed at temp files; the live ones are never
written.
"""
import importlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))
sys.path.insert(0, str(REPO / "scripts"))

import check_workspace as cw  # noqa: E402  -- its SECRET_PATTERN is the independent oracle

PASSED = FAILED = 0
# Real-shaped, and NOT real: the whole point of these strings is that a pattern cannot tell.
FAKE_KEY = "sk-ant-" + "A" * 30
FAKE_GH = "ghp_" + "b" * 36


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def check_true(name, got):
    check(name, bool(got), True)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="t1563-"))
    try:
        import console
        console = importlib.reload(console)
        log, hits = tmp / "manager-actions.jsonl", tmp / "private" / "secret-hits.jsonl"
        console.ACTIONS = log
        console.SECRET_HITS = hits

        # 0. The writer's pattern IS the scanner's pattern. Two patterns that drift apart is
        #    how a fix in one place leaves the other red -- this fails the moment they differ.
        check("the writer scans exactly what the checker scans", console.SECRET_RE.pattern, cw.SECRET_PATTERN)

        # 1. A command carrying a key shape is written WITHOUT one.
        console._action({"tool": "run_command", "command": f"curl -H 'x-api-key: {FAKE_KEY}' http://x",
                         "exit": 0})
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        check("the log holds no secret-shaped string", bool(re.search(cw.SECRET_PATTERN, json.dumps(row))), False)
        check("the row is tagged, so a capability policy can gate it", row.get("sensitivity"), "secret-shaped")
        marker = row["command"]
        check_true("the marker keeps the identity and the length",
                   "sha256:" in marker and f"len:{len(FAKE_KEY)}" in marker)
        check_true("the rest of the command is intact", marker.startswith("curl -H") and marker.endswith("http://x"))

        # 2. The raw value is preserved, whole, in the private ledger with its sensitivity tag.
        hrows = [json.loads(l) for l in hits.read_text(encoding="utf-8").splitlines() if l.strip()]
        check("the private ledger has the record", len(hrows), 1)
        check("the raw value is kept whole", hrows[0].get("raw"), FAKE_KEY)
        check("it carries its sensitivity label", hrows[0].get("sensitivity"), "secret-shaped")
        check("its sha256 matches the marker", len(hrows[0].get("sha256") or ""), 64)
        check_true("the marker's short hash is that sha256's prefix",
                   f"sha256:{hrows[0]['sha256'][:8]}" in marker)
        check("it records which tool wrote it", hrows[0].get("tool"), "run_command")

        # 3. A second key shape in the same command is caught too -- one pattern, not a list.
        console._action({"tool": "run_command", "command": f"export GITHUB_TOKEN={FAKE_GH}"})
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        check("a second credential shape is caught as well",
              bool(re.search(cw.SECRET_PATTERN, json.dumps(row))), False)
        check("and the private ledger now has two records",
              len([l for l in hits.read_text(encoding="utf-8").splitlines() if l.strip()]), 2)

        # 4. A clean command is written unchanged, untagged, and adds nothing to the private ledger.
        before = len([l for l in hits.read_text(encoding="utf-8").splitlines() if l.strip()])
        console._action({"tool": "run_command", "command": "git status --short", "exit": 0})
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        check("a clean command is written verbatim", row.get("command"), "git status --short")
        check("  and is not tagged", "sensitivity" in row, False)
        after = len([l for l in hits.read_text(encoding="utf-8").splitlines() if l.strip()])
        check("  and writes nothing to the private ledger", after, before)

        # 5. A secret nested in a structure is redacted too: rows carry outputs, not only argv.
        console._action({"tool": "web_fetch", "url": "http://x", "body": {"nested": [f"k={FAKE_KEY}"]}})
        row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
        check("a nested value is redacted", bool(re.search(cw.SECRET_PATTERN, json.dumps(row))), False)
        check_true("  and the marker is in the nested string", "sha256:" in row["body"]["nested"][0])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
