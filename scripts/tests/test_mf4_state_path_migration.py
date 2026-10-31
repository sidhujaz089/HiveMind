#!/usr/bin/env python3
"""mf4 oracle: a state path cannot be renamed without a migration, and a state writer
reports a missing directory instead of creating it.

Seen: the scoping-queue rename moved the code but not the live directory, and a writer's
mkdir(parents=True) raced the migration by resurrecting the old layout.

(a) scripts/state_path_gate.py, run against a TEMPORARY root (the live paths do not exist
    here): removing a workspace/run-logs/ literal while the old path has writes inside 24h
    is a finding; stale old path, an added-only literal, a staged migration script, or a
    staged test that still carries the old literal each clear it.
(b) dispatch_loop.write_tick_row no longer creates its latest-tick parent: a missing
    directory is REPORTED (the tick still lands in the ledger) and nothing is created.

RED before the fix: state_path_gate.py does not exist and write_tick_row mkdirs the parent.
"""
import io
import sys
import tempfile
import time
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

FAILS = []


def check(label, cond, detail=""):
    line = "  [%s] %s" % ("ok" if cond else "BAD", label)
    if not cond and detail:
        line += " -- " + detail
    print(line)
    if not cond:
        FAILS.append(label)


OLD = 'LOG = REPO / "workspace/run-logs/scoping-queue/out.jsonl"\n'
NEW = 'LOG = REPO / "workspace/run-logs/manager-queue/out.jsonl"\n'
LIT = "workspace/run-logs/scoping-queue/out.jsonl"


def main():
    print("mf4 oracle: state renames need a migration; writers never mkdir state\n")
    try:
        import state_path_gate
    except ImportError as e:
        check("scripts/state_path_gate.py exists and imports", False, str(e))
    else:
        check("scripts/state_path_gate.py exists and imports", True)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            live = root / LIT
            live.parent.mkdir(parents=True)
            live.write_text('{"row":1}\n', encoding="utf-8")

            finds = state_path_gate.gate({"scripts/q.py": (OLD, NEW)}, root=root)
            check("renaming away a literal whose old path was written in the last 24h is refused",
                  any(l == LIT for _f, l, _w in finds), "%r" % (finds,))
            check("  ...and the refusal names a migration script or an old-layout test",
                  any("migrat" in w for _f, _l, w in finds), "%r" % (finds,))

            finds = state_path_gate.gate(
                {"scripts/q.py": (OLD, NEW),
                 "scripts/migrate_scoping_queue.py": ("", "move the directory\n")}, root=root)
            check("a staged migration script clears the refusal", finds == [], "%r" % (finds,))

            finds = state_path_gate.gate(
                {"scripts/q.py": (OLD, NEW),
                 "scripts/tests/test_queue_dir_migration2.py":
                     ("", f'OLD = "{LIT}"  # starts from the old layout\n')}, root=root)
            check("a staged test that starts from the old layout clears it too", finds == [],
                  "%r" % (finds,))

            cold = time.time() + 2 * 24 * 3600
            finds = state_path_gate.gate({"scripts/q.py": (OLD, NEW)}, root=root, now=cold)
            check("an old path with no writes in 24h is not a refusal", finds == [],
                  "%r" % (finds,))

            finds = state_path_gate.gate({"scripts/q.py": ("x = 1\n", OLD)}, root=root)
            check("ADDING a state literal is never refused (only renaming away is)",
                  finds == [], "%r" % (finds,))

            live.unlink()
            finds = state_path_gate.gate({"scripts/q.py": (OLD, NEW)}, root=root)
            check("an old path that does not exist is not a refusal", finds == [],
                  "%r" % (finds,))

        pc = (ROOT / "scripts" / "hooks" / "pre-commit").read_text(encoding="utf-8")
        check("pre-commit is wired to the gate",
              "state_path_gate" in pc)  # text-assert: the hook is shell; the wiring line is the only mechanical pin available

    # (b) the writer reports a missing state directory instead of creating it.
    import dispatch_loop
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "run-logs-renamed-away" / "dispatch-tick-latest.json"
        rows = []
        err = io.StringIO()
        with redirect_stderr(err):
            dispatch_loop.write_tick_row(running=[], priority=[], started=[], preempted=[],
                                         queued=[], skipped=[], idle=True,
                                         record=rows.append, latest_tick_path=missing)
        check("a missing state directory is NOT created by the tick writer",
              not missing.parent.exists(),
              "the writer resurrected the directory -- the mf4 race")
        check("  ...the miss is REPORTED", "missing" in err.getvalue() or "mf4" in err.getvalue(),
              "stderr: %r" % (err.getvalue()[:160],))
        check("  ...and the tick still lands in the ledger (report, not a crash)",
              len(rows) == 1 and rows[0].get("event") == "tick", "%r" % (rows,))

        ok_path = Path(td) / "exists" / "dispatch-tick-latest.json"
        ok_path.parent.mkdir(parents=True)
        with redirect_stderr(io.StringIO()):
            dispatch_loop.write_tick_row(running=[], priority=[], started=[], preempted=[],
                                         queued=[], skipped=[], idle=True,
                                         record=rows.append, latest_tick_path=ok_path)
        check("a PRESENT state directory is written exactly as before", ok_path.is_file())

    n = len(FAILS)
    print("\n%d failed -- %s" % (n, "state renames are gated and writers report, not create"
                                 if not n else "mf4 is not in place"))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
