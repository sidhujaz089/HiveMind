#!/usr/bin/env python3
"""Fixtures for search scope (Chat's handover section 9).

Chat reported three defects. The dangerous one is the DEFAULT GLOB: it was `**/*.md`, which
could not reach the corpus at all -- chat/ is 261 .txt files against 2 .md. A new agent using
the default got a plausible `scanned` count and zero hits from the very material the bridge
exists to serve. That is not an empty result, it is a wrong answer wearing one.

So the assertions are about what the DEFAULT reaches and what it quietly does not, and every
exclusion must be COUNTED in the response -- a caller has to be able to tell "searched
everything, found nothing" from "did not look there".
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from workspace_mcp.core import Workspace  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def build():
    tmp = Path(tempfile.mkdtemp())
    files = {
        "chat/projects/family/note.txt": "the needle is here in the corpus",
        "docs/readme.md": "the needle is here in a document",
        "chat/export/raw/data-abc.txt": "the needle is here in the frozen dump",
        "projects/app/.venv/lib/python3.12/site-packages/x/y.py": "the needle in a dependency",
        "projects/app/node_modules/z/index.js": "the needle in node_modules",
        "workspace/private/secret.md": "the needle in a denied path",
        "assets/photo.png": "the needle in a binary",
    }
    for name, body in files.items():
        f = tmp / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return tmp, Workspace(tmp, caller="fixture")


def main():
    tmp, ws = build()
    try:
        r = ws.search("needle")
        paths = {h["path"] for h in r["hits"]}

        print("The default search must reach the corpus (the whole point):")
        report("finds the .txt in chat/", "chat/projects/family/note.txt" in paths, True)
        report("finds the .md in docs/", "docs/readme.md" in paths, True)

        print("\nAnd must NOT return noise or denied paths:")
        report("no dependency hits",
               any("site-packages" in p for p in paths), False)
        report("no node_modules hits", any("node_modules" in p for p in paths), False)
        report("no workspace/private hits", any("private" in p for p in paths), False)
        report("no frozen export hits", any("export/raw" in p for p in paths), False)

        print("\nEvery exclusion is COUNTED, so a caller can tell it was not searched:")
        report("dependency files counted", r["skipped_dependency_files"] > 0, True)
        report("frozen export counted", r["skipped_frozen_export"], 1)
        report("non-text counted", r["skipped_non_text"] > 0, True)

        print("\nThe frozen export is out of the way, NOT unreachable:")
        r2 = ws.search("needle", glob="chat/export/raw/**/*")
        report("explicit glob reaches it", len(r2["hits"]), 1)

        print("\nThe old default would have missed the corpus -- proving the bug was real:")
        r3 = ws.search("needle", glob="**/*.md")
        report("'**/*.md' finds no corpus .txt",
               any(h["path"].endswith("note.txt") for h in r3["hits"]), False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

        print("\nThe SERVED default, not just the library one (Chat, 2026-08-25):")
        # t059 fixed WorkspaceCore.search's default and left server.py's tool signature
        # at **/*.md. Every real search Chat ran goes through server.py, so the corpus
        # stayed invisible for five days while this suite passed. Assert the layer the
        # callers actually touch, not the one that was convenient to import.
        import re as _re
        srv = (Path(__file__).resolve().parent.parent / "workspace_mcp"
               / "server.py").read_text()
        m = _re.search(r'def search\(query: str, glob: str = "([^"]+)"\)', srv)
        report("server.py's default reaches the whole corpus",
               m.group(1) if m else "MISSING", "**/*")
        d = _re.search(r"@app\.tool\(description=(.*?)\)\ndef search", srv, _re.S)
        report("and its description still promises that",
               bool(d and "ALL text files" in d.group(1)), True)

        print("\nA counter must not report 'skipped nothing' for a scan that could not:")
        tmp2, ws2 = build()
        try:
            a = ws2.search("needle", glob="workspace/**/*.md")
            report("skipped_frozen_export is null when out of the glob's reach",
                   a["skipped_frozen_export"], None)
            b = ws2.search("needle")
            report("and a number when the default search can reach it",
                   isinstance(b["skipped_frozen_export"], int), True)
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

    print(f"\n{PASSED} passed, {FAILED} failed")

    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
