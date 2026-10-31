#!/usr/bin/env python3
"""Fixture for the served-prose check (L-085).

The check must FAIL on the exact sentence that shipped for weeks:
"chat/ and workspace/private/ are not served." -- false for chat/, and false in the direction
nobody audits, because a client told a capability is absent never exercises it and therefore
never produces an error.

Passing on the corrected sentence proves nothing on its own. The assertion that matters is
that the original, wrong one is caught.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


CORE = '''
READ_ONLY_PREFIXES = ("chat/",)
DENIED_PREFIXES = ("workspace/private/", ".git/", ".venv/")
'''

def server_src(sentence):
    return 'app = MCPServer(\n    name="x",\n    instructions=(\n        "%s"\n    ),\n)\n' % sentence


def verdict(sentence):
    tmp = Path(tempfile.mkdtemp())
    try:
        subprocess.run(["git", "init", "-q", str(tmp)], check=True, capture_output=True)
        (tmp / "projects").mkdir()
        wm = tmp / "scripts" / "workspace_mcp"
        wm.mkdir(parents=True)
        (wm / "core.py").write_text(CORE)
        (wm / "server.py").write_text(server_src(sentence))
        out = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_workspace.py"),
             "--quick", "--repo-root", str(tmp)], capture_output=True, text=True).stdout
        if "Prose the system serves about itself" not in out:
            return "ABSENT"
        block = []
        for line in out.split("Prose the system serves about itself", 1)[1].splitlines()[1:]:
            if line and not line.startswith(" "):
                break
            block.append(line)
        block = "\n".join(block)
        return "FAIL" if "[FAIL]" in block else ("PASS" if "[PASS]" in block else "ABSENT")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("The check must REFUSE the sentence that actually shipped:")
    report("the L-085 sentence, verbatim",
           verdict("chat/ and workspace/private/ are not served."), "FAIL")
    report("the same claim worded differently",
           verdict("Note that chat/ is not served to any client."), "FAIL")

    print("\nAnd it must accept a description that matches the constants:")
    report("chat/ described as read-only, private named",
           verdict("chat/ is served READ-ONLY; writes refuse. workspace/private/ is not "
                   "served at all."), "PASS")

    print("\nA denied path the prose never mentions is also a gap:")
    report("workspace/private/ unmentioned",
           verdict("chat/ is served read-only."), "FAIL")

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
