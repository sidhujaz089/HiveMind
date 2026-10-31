#!/usr/bin/env python3
"""D.5.1 (RCA D-production-stall-rca.md): test_workspace_mcp.py pins its interpreter to
<repo>/.venv/bin/python, and .venv is gitignored -- so it is ABSENT in every delivery worktree,
where the pre-commit related_tests gate runs the suite. Result: 8 builds' commits refused with
exit 3 "accepted but NOT WRITTEN" (all 8 since 10-20, one cause), 453 sandbox rows red at
baseline, and every future coder.py commit blocked the same way.

The interpreter a suite means by "the venv's python" is the interpreter RUNNING the suite
(sys.executable) -- that is what the .venv path was a proxy for. This oracle makes the suite
runnable in a checkout that has no .venv at all: it runs on a plain interpreter from a directory
with no .venv, and the subprocess-launching checks (bind_exit et al.) must not FileNotFoundError.

Isolation: a copy of the suite and its import base in a temp dir OUTSIDE the repository, with a
system python3 that has no .venv above it; the live checkout's .venv is never involved, and the
live suite file is never touched.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "scripts" / "tests" / "test_workspace_mcp.py"
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def main():
    # The suite's source must not pin an interpreter by path from __file__ (match the
    # assignment, not prose: the comment explaining the old pin quotes the old path).
    import re
    src = SUITE.read_text(encoding="utf-8")
    pinned = re.search(r'^VENV_PY\s*=\s*Path\(\s*__file__', src, re.M)
    check("the suite does not pin an interpreter by path from __file__", bool(pinned), False)
    check("the suite resolves its server interpreter from sys.executable",
          "sys.executable" in src, True)

    # And it must RUN and PASS where no .venv exists. Copy it -- with the import base it needs
    # (repo_root/scripts, including chat_agent.py, which workspace_mcp.core imports) -- into a
    # temp tree that has no .venv anywhere above it. Temp dirs live under ~/.cache/hivemind-tmp,
    # never /tmp (it is RAM).
    cache = Path.home() / ".cache" / "hivemind-tmp"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fix3-venvfree-", dir=str(cache)) as tmp:
        tmp = Path(tmp)
        repo = tmp / "repo"
        (repo / "scripts" / "tests").mkdir(parents=True)
        shutil.copytree(ROOT / "scripts" / "workspace_mcp", repo / "scripts" / "workspace_mcp")
        shutil.copy(SUITE, repo / "scripts" / "tests" / "test_workspace_mcp.py")
        # The suite's import graph reaches top-level scripts/ modules (chat_agent, ...): give
        # the copy the same import surface as the repository.
        for f in (ROOT / "scripts").glob("*.py"):
            shutil.copy(f, repo / "scripts" / f.name)
        # workspace_mcp.core resolves projects/app-repo by path from its own file (for
        # taskstore): mirror that layout in the copy (test files not needed).
        app = ROOT / "projects" / "app-repo"
        (repo / "projects" / "app-repo").mkdir(parents=True)
        for f in app.glob("*.py"):
            shutil.copy(f, repo / "projects" / "app-repo" / f.name)
        # The suite copies the repo's commit-msg hook into its fixture repos (its writes
        # commit through it). Give the copy one too.
        hooks = repo / "scripts" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy(ROOT / "scripts" / "hooks" / "commit-msg", hooks / "commit-msg")
        # The suite reads/writes paths under REPO_ROOT relative to its own file (chat/ fixtures,
        # workspace/inbox). Give the copy a minimal live-shaped tree; the suite creates what it needs.
        (repo / "chat").mkdir()
        (repo / "workspace" / "inbox").mkdir(parents=True)
        (repo / "docs").mkdir()
        r = subprocess.run([sys.executable, str(repo / "scripts" / "tests" / "test_workspace_mcp.py")],
                           cwd=str(repo), capture_output=True, text=True, timeout=600)
        out = (r.stdout + r.stderr)[-4000:]
        check("the suite exits 0 with NO .venv in its tree", r.returncode, 0)
        check("  and reports no failures", "[BAD]" in out, False)
        check("  and its own numbers print", "passed" in out, True)
        check("  no traceback in the suite's output", "Traceback (most recent call last)" in out, False)
        check("  no missing-interpreter error from the old pin",
              "No such file or directory: '" in out and ".venv" in out, False)
        check("  no 'No such file or directory: .*python' from a pinned path",
              ".venv" in out, False)
        if FAILED:
            print("---- suite output (tail) ----")
            print("\n".join(out.splitlines()[-30:]))
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
