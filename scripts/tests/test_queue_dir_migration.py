#!/usr/bin/env python3
"""Renaming a directory is not the same as moving what is in it (Claude's send-back, 2026-10-31).

My first attempt renamed workspace/run-logs/scoping-queue -> scoping-queue in the CODE and moved nothing. Claude caught
it: the live directory is 164 MB and holds GRANT, claims/, custom/, in/, out/ and a 1.7 MB progress.log, so landing it
would orphan all of it and scoping_waiting() would find no progress.log and call the queue dead.

So the rename ships with a MIGRATION, and the migration refuses the ways it could destroy state:

  * both directories present  -> refuse (which one is the truth? a person must say);
  * neither present           -> refuse (nothing to move; say so rather than creating an empty queue);
  * the loops are RUNNING      -> refuse, because a worker mid-task holds claims and writes into out/;
  * same filesystem            -> a rename, so 164 MB is not copied and a half-move cannot happen.

And the code, when the new path is missing but the old one is there, must SAY so rather than report an idle queue.

RED ON MAIN: scripts/migrate_queue_dir.py does not exist.
"""
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import migrate_queue_dir as mq          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    old, new = root / "manager-queue", root / "scoping-queue"

    ok, why = mq.migrate(old, new, running=False, apply=False)
    check("neither directory present is REFUSED", ok is False and "neither" in why.lower(), why)

    old.mkdir(); (old / "progress.log").write_text("x\n"); (old / "claims").mkdir()
    ok, why = mq.migrate(old, new, running=False, apply=False)
    check("a dry run reports the move without doing it", ok is True and old.exists() and not new.exists(), why)

    ok, why = mq.migrate(old, new, running=True, apply=True)
    check("RUNNING loops are refused -- a worker mid-task holds claims", ok is False and "running" in why.lower(), why)

    ok, why = mq.migrate(old, new, running=False, apply=True)
    check("the move happens, contents and all", ok is True and new.exists() and not old.exists(), why)
    check("  and the state came with it", (new / "progress.log").is_file() and (new / "claims").is_dir(), "")

    old.mkdir(); (old / "x").write_text("y")
    ok, why = mq.migrate(old, new, running=False, apply=True)
    check("both present is REFUSED -- which one is the truth needs a person", ok is False and "both" in why.lower(), why)

# WRITE_GRANT MUST NOT CREATE THE DIRECTORY (Claude, 2026-10-31): a scheduler tick that mkdirs recreates the queue
# directory under any rename, so the race lives in write_grant, not in the deploy order.
import scoping_lanes as sl
with tempfile.TemporaryDirectory() as td:
    missing = Path(td) / "nope"
    wrote = sl.write_grant(1, q=missing)
    check("write_grant does NOT create the queue directory", wrote is False and not missing.exists(),
          "a tick would recreate the directory under the migration")

check("the migration asks systemd for its instances, not a hard-coded pair",
      "list-units" in (HERE / "scripts" / "migrate_queue_dir.py").read_text(encoding="utf-8"))

src = (HERE / "scripts" / "dispatch_loop.py").read_text(encoding="utf-8")
check("the dispatcher NAMES the old directory when the new one is missing",
      "manager-queue" in src and ("MISSING" in src.upper()),
      "a missing queue directory would read as an idle queue")

print("test_queue_dir_migration:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
