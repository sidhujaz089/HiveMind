#!/usr/bin/env python3
"""Hand out build-chunk numbers (the p in p62-t276-...) that never repeat.

    python3 scripts/pnumber.py            # show the last number handed out

Jas, 2026-09-28: "Is it possible to make the p numbers never repeat?" Two writers numbered
chunks as "highest row in scripts/autonomy_loop.sh + 1": bridge.py (reading a bridge/<task>
worktree's copy) and library.enroll_compliance (reading main). Each saw only its own copy, so
both took p60 on 2026-09-28 and the merge of bridge/t276 conflicted.

One counter, in git's COMMON dir -- the one directory main and every worktree share -- held
under an exclusive file lock while a number is claimed. A number is max(counter, highest row
the caller can see) + 1, so the counter can only ever move forward, and it catches up on its
own if a row was ever added by hand. Outside a git repository (a test fixture) there is no
shared state to protect, so the old rule applies: highest seen + 1, nothing written.
"""
import fcntl
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _counter(repo):
    """The shared counter file, or None when repo is not inside a git repository."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  NOTE: git unavailable for the p counter ({e}); numbering locally",
              file=sys.stderr)
        return None
    if r.returncode != 0:
        return None
    common = Path(r.stdout.strip())
    if not common.is_absolute():
        common = (Path(repo) / common).resolve()
    return common / "p-counter"


def claim(repo, seen_max, count=1):
    """Reserve `count` consecutive numbers above everything seen or handed out; return the first."""
    f = _counter(repo)
    if f is None:
        return seen_max + 1
    with open(f, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        raw = fh.read().strip()
        last = int(raw) if raw.isdigit() else 0
        first = max(last, seen_max) + 1
        fh.seek(0)
        fh.truncate()
        fh.write(f"{first + count - 1}\n")
        fh.flush()
    return first


def main(argv):
    f = _counter(REPO)
    if f is None:
        print("not a git repository: no shared counter")
        return 1
    print(f.read_text().strip() if f.is_file() else "none handed out yet")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
