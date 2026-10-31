#!/usr/bin/env python3
"""ONE-TIME: rewrite every task file's `assigned:` as `needs:` (t9540).

    python3 scripts/migrate_needs_key.py            # dry run: how many files would change
    python3 scripts/migrate_needs_key.py --apply    # rewrite them

WHY: the file said `assigned:` while every reader asked for `needs:`, so one value had two names. That is the
fault this whole piece of work exists to remove -- a reader comparing the wrong name is exactly how
`assigned_worker.pending()` went silent after the tier migration. `find()` still READS `assigned:` so nothing
breaks while this runs; `_rewrite()` no longer writes it.

WHAT IT DOES NOT DO: it adds no note. This is a rename, not a reassignment or a decision, and a note per task
would put 700 lines into the record that say nothing happened.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "projects" / "app-repo"))
import taskstore as T  # noqa: E402


def needs_rename(path):
    """True when the FILE still says `assigned:` in its front matter."""
    try:
        head = Path(path).read_text(encoding="utf-8", errors="replace").split("---")[1]
    except (OSError, IndexError):
        return False
    return any(ln.startswith("assigned:") for ln in head.splitlines())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--apply", action="store_true", help="rewrite the files (default: report only)")
    a = ap.parse_args(argv)

    ids = T.read_queue()
    todo = []
    for tid in ids:
        meta, _ = T.find(tid)
        if meta and needs_rename(meta.get("path")):
            todo.append(tid)
    print(f"  tasks read: {len(ids)} | files still saying assigned:: {len(todo)}")
    if not a.apply:
        print("  dry run -- nothing written. Re-run with --apply.")
        return 0
    done = 0
    for tid in todo:
        meta, body = T.find(tid)
        T._rewrite(meta, body)                       # the same writer every other change uses
        done += 1
    left = sum(1 for tid in ids if needs_rename((T.find(tid)[0] or {}).get("path")))
    print(f"  rewrote {done} | still saying assigned:: {left}")
    if left:
        print("  REFUSED TO CALL IT DONE: some files still carry the old key.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
