#!/usr/bin/env python3
"""Points git at the tracked hooks directory. Run once per clone.

Git does not clone hooks. Without core.hooksPath pointing at scripts/hooks,
nothing enforces the commit format, the Agent trailer, or the secret scan -- and
the first anyone knows is when the compliance audit finds a month of
unattributed commits.

This does not copy anything. core.hooksPath points git at the tracked directory,
so there is no second copy to drift out of step.

    python3 scripts/install_hooks.py

Ported from install-hooks.ps1.
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = ("pre-commit", "commit-msg", "pre-push")
_TTY = sys.stdout.isatty()


def _c(code, text):
    return f"\033[{code}m{text}\033[0m" if _TTY else text


def git(*args):
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True,
        timeout=60,
    )


def main():
    print()
    print(f"Installing git hooks for {REPO_ROOT}")
    print("  " + git("--version").stdout.strip())

    git("config", "core.hooksPath", "scripts/hooks")
    current = git("config", "core.hooksPath").stdout.strip()
    if current != "scripts/hooks":
        print(_c("31", f"  FAILED: core.hooksPath is '{current}'"))
        return 1
    print(_c("32", "  core.hooksPath -> scripts/hooks"))

    problems = 0
    for hook in HOOKS:
        rel = f"scripts/hooks/{hook}"
        path = REPO_ROOT / rel
        if not path.is_file():
            print(_c("31", f"  MISSING: {rel}"))
            problems += 1
            continue

        # On Unix a hook without the execute bit is silently skipped -- the worst
        # possible failure for a safety check. Set it on disk AND record it in the
        # index, so it survives a clone.
        #
        # The Drive mount could not express the exec bit at all, which is how
        # core.hooksPath came to be set while all three hooks silently never ran.
        # So the bit is read back and reported rather than assumed.
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        git("update-index", "--chmod=+x", "--", rel)
        entry = git("ls-files", "-s", "--", rel).stdout.strip()

        on_disk_ok = os.access(path, os.X_OK)
        in_index_ok = entry.startswith("100755")

        if on_disk_ok and in_index_ok:
            print(_c("32", f"  {rel}  executable (disk and index)"))
        elif not entry:
            print(_c("33", f"  {rel}  not tracked by git yet -- commit it, then re-run"))
            problems += 1
        else:
            detail = []
            if not on_disk_ok:
                detail.append("not executable on disk")
            if not in_index_ok:
                detail.append(f"index mode {entry.split(' ')[0]}")
            print(_c("33", f"  {rel}  {', '.join(detail)}"))
            print(_c("90", "    A clone would skip this hook silently."))
            problems += 1

    print()
    print(_c("36", "  Active hooks:"))
    print("    pre-commit  blocks credentials, Drive stub files, empty markdown")
    print("    commit-msg  enforces <type>(<project>): <summary> and the Agent trailer")
    print("    pre-push    refuses to push when check_workspace.py fails")
    print()
    if problems:
        print(_c("33", f"  Done, with {problems} thing(s) needing attention above."))
        print()
        return 1
    print(_c("32", "  Done. Hooks are live in this clone."))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
