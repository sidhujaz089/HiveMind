#!/usr/bin/env python3
"""Move the scoping queue's state directory when its NAME changes, and refuse to destroy it.

Claude's send-back, 2026-10-31: *"THE RENAME HAS NO MIGRATION, and that is a live-state bug, not a naming one.
~/ai-workspace/workspace/run-logs/manager-queue/ is the live directory: GRANT, claims/, in/, out/, progress.log (1.7 MB),
station2.jsonl. run-logs/scoping-queue/ does not exist, and nothing in the branch moves it."*

He was right: I renamed a path in the code and left 164 MB of live state behind it. A directory rename is not a text
substitution, and the state is not decoration -- `claims/` is which worker holds which task, `GRANT` is how many lanes the
scheduler gave the loop, and `progress.log` is what `scoping_waiting()` reads to decide the queue is alive.

So the rename ships with this, and it REFUSES the four ways it could go wrong:

  * NEITHER directory present -- nothing to move; say so rather than create an empty queue that looks healthy;
  * BOTH present -- refuse and name them; which one is the truth is a person's decision, not a guess;
  * the loops RUNNING -- refuse, because a worker mid-task holds a claim and writes into out/ while we move it under it;
  * a COPY instead of a move -- refused by construction: os.rename within one filesystem, and a cross-device move is
    refused rather than half-done (164 MB copied badly is worse than not copied).

    python3 scripts/migrate_queue_dir.py                 # say what would happen
    python3 scripts/migrate_queue_dir.py --apply         # move it, after stopping both instances

The code that READS the directory also has to say when it is missing -- see dispatch_loop's queue_dir_missing_note():
a renamed path with no migration must read as "the directory is missing", never as "the queue is idle".
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOGS = REPO / "workspace" / "run-logs"
OLD = LOGS / "manager-queue"
NEW = LOGS / "scoping-queue"
# ASK SYSTEMD, DO NOT LIST THEM (Claude, 2026-10-31): the unit is a TEMPLATE and the scoper comes from the Roles tab, so
# a third scoper would never be in a hard-coded pair and its loop could be running mid-move unnoticed.



def instances():
    """Every scoping-queue instance systemd knows about, whatever the scoper is called."""
    try:
        r = subprocess.run(["systemctl", "--user", "list-units", "scoping-queue@*", "--all", "--no-legend",
                            "--plain"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:               # noqa: BLE001 -- said, not swallowed
        return None, f"systemctl unreadable ({type(e).__name__}: {e})"
    out = []
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if parts and parts[0].startswith("scoping-queue@"):
            out.append((parts[0], parts[3] if len(parts) > 3 else ""))
    return out, ""


def _running(units=None):
    """(True, [active units]) when any scoping-queue instance is up. Anything unreadable counts as running."""
    if units is None:
        found, err = instances()
        if found is None:
            return True, [err]
        units = [f"{u}" for u, _ in found]
    active = []
    for u in units:
        try:
            r = subprocess.run(["systemctl", "--user", "is-active", u], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as e:           # noqa: BLE001 -- said, not swallowed
            return True, [f"{u} (state unknown: {type(e).__name__})"]
        if (r.stdout or "").strip() == "active":
            active.append(u)
    return bool(active), active


def migrate(old=None, new=None, running=None, apply=False):
    """(ok, why). Moves `old` to `new` only when that is unambiguous and nothing is using it."""
    old, new = Path(old or OLD), Path(new or NEW)
    if not old.exists() and not new.exists():
        return False, (f"neither {old} nor {new} exists -- there is no queue state to move, so nothing was done "
                       f"(an empty directory here would look like a healthy idle queue)")
    if old.exists() and new.exists():
        return False, (f"both {old} and {new} exist -- refusing to guess which holds the real queue state. "
                       f"Move or remove one by hand, then re-run")
    if not old.exists():
        return True, f"already migrated: {new} exists and {old} does not (nothing to do)"
    was_running, active = (_running() if running is None else (running, [f"running={running}"] if running else []))
    if was_running:
        return False, (f"the scoping loops are RUNNING ({', '.join(active) or 'state unknown'}) -- a worker mid-task "
                       f"holds a claim and writes into out/; stop them first:\n"
                       f"  systemctl --user stop scoping-queue@manager scoping-queue@auditor")
    try:
        same_fs = os.stat(old).st_dev == os.stat(old.parent).st_dev
    except OSError as e:                                            # noqa: BLE001 -- said, not swallowed
        return False, f"cannot stat {old} ({type(e).__name__}: {e})"
    if not same_fs:
        return False, f"{old} and its parent are on different filesystems -- refusing a copy that could be half-done"
    if not apply:
        return True, f"would move {old} -> {new} ({_size(old)}); re-run with --apply"
    try:
        os.rename(old, new)
    except OSError as e:                                            # noqa: BLE001 -- said, not swallowed
        return False, f"could not move {old} -> {new} ({type(e).__name__}: {e}); nothing was half-moved"
    return True, f"moved {old} -> {new}"


def _size(p):
    total = 0
    for f in Path(p).rglob("*"):
        try:
            total += f.stat().st_size
        except OSError as e:                                        # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: could not size {f} ({type(e).__name__}: {e}) -- the total is a lower bound", file=sys.stderr)
    return f"{total / 2**30:.1f} GB"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Move the scoping queue's state directory to its role-neutral name.")
    ap.add_argument("--apply", action="store_true", help="do it (the default only says what would happen)")
    ap.add_argument("--old", default=None)
    ap.add_argument("--new", default=None)
    a = ap.parse_args(argv)
    ok, why = migrate(a.old, a.new, apply=a.apply)
    print(f"  {'OK' if ok else 'REFUSED'}: {why}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
