#!/usr/bin/env python3
"""mf4(a): a state path cannot be renamed without a migration.

Seen: the scoping-queue rename moved the code but not the live directory -- readers looked
in the new place, the writers' old files sat where they were, and mkdir(parents=True) in a
writer resurrected the old layout mid-migration.

The gate: when a staged change REMOVES a path literal under workspace/run-logs/ or
~/.cache/workspace/ from a file, and that old path exists on disk with writes in the last
24 hours, the commit is refused -- unless the branch also stages a migration script
(scripts/migrate_*.py) or a test that starts from the old layout (a staged file whose new
text still carries the old literal).

Pure functions take the staged texts and a root, so the whole gate is testable against a
temporary directory; the live paths need not exist here.

CLI:  python3 scripts/state_path_gate.py --staged     # from pre-commit; exit 1 on findings
"""
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FRESH_S = 24 * 3600
_LIT = re.compile(r"(?:~/\.cache/workspace|workspace/run-logs)/[A-Za-z0-9._][A-Za-z0-9._/-]*")


def literals(text):
    """Every state-path literal in `text` (workspace/run-logs/... or ~/.cache/workspace/...)."""
    return set(_LIT.findall(text or ""))


def _resolve(lit, root, home):
    if lit.startswith("~/"):
        return Path(home or Path.home()) / lit[2:]
    return Path(root) / lit


def gate(staged, root=REPO, home=None, fresh_s=FRESH_S, now=None):
    """[(file, old_literal, why)] -- the refusals for this staged set.

    `staged` maps a file path to (old_text, new_text): the committed content and the staged
    content. A literal present in old_text and absent from new_text is a rename-away; it is
    refused while the OLD path exists with writes inside `fresh_s`, unless a staged
    migration script (scripts/migrate_*.py) or any staged file still carrying the literal
    (a test that starts from the old layout) comes with it."""
    now = time.time() if now is None else now
    carried = {p for p, (_o, new) in staged.items()
               if Path(p).name.startswith("migrate_") and str(p).endswith(".py")}
    out = []
    for path, (old_text, new_text) in sorted(staged.items()):
        gone = literals(old_text) - literals(new_text)
        for lit in sorted(gone):
            escort = bool(carried) or any(
                q != path and lit in new2 for q, (_o2, new2) in staged.items())
            if escort:
                continue
            p = _resolve(lit, root, home)
            try:
                fresh = p.exists() and (now - p.stat().st_mtime) < fresh_s
            except OSError:
                fresh = False
            if fresh:
                out.append((path, lit,
                            f"renames away a LIVE state path: {lit} exists and was written "
                            f"in the last 24h. Stage a migration (scripts/migrate_*.py) or "
                            f"a test that starts from the old layout (mf4)."))
    return out


def staged_from_git(root=REPO):
    r = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--name-only",
                        "--diff-filter=ACM"], capture_output=True, text=True, timeout=60)
    staged = {}
    for p in filter(None, r.stdout.splitlines()):
        if not p.endswith((".py", ".sh")) and not p.startswith("scripts/hooks/"):
            continue
        old = subprocess.run(["git", "-C", str(root), "show", f"HEAD:{p}"],
                             capture_output=True, text=True, timeout=60)
        new = subprocess.run(["git", "-C", str(root), "show", f":{p}"],
                             capture_output=True, text=True, timeout=60)
        staged[p] = (old.stdout if old.returncode == 0 else "",
                     new.stdout if new.returncode == 0 else "")
    return staged


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if args and args[0] == "--staged":
        finds = gate(staged_from_git())
        for f, lit, why in finds:
            print(f"  {f}: {why}")
        print(f"{len(finds)} state-path finding(s)" if finds
              else "clean: no live state path renamed without a migration")
        return 1 if finds else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
