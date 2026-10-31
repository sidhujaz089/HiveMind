#!/usr/bin/env python3
"""stuck_scheduler.restart(): one restart per cooldown, through the one sudoers rule, recorded.
Never restarts anything here -- subprocess.run and record are replaced."""
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import stuck_scheduler as S  # noqa: E402

FAILED = 0


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


def main():
    import subprocess
    calls, rows = [], []
    real_run, real_rec, real_last = subprocess.run, S.record, S.LAST_RESTART
    subprocess.run = lambda cmd, **kw: (calls.append(cmd), types.SimpleNamespace(returncode=0, stdout="", stderr=""))[1]
    S.record = lambda row, path=None: rows.append(row)
    S.LAST_RESTART = Path(tempfile.mkdtemp()) / "last"
    try:
        report("a first wedge restarts", S.restart(100000.0), True)
        report("through exactly the sudoers command", calls[-1],
               ["sudo", "-n", "/usr/bin/systemctl", "restart", "ollama"])
        report("and records it", rows[-1]["status"], "restarted")
        report("a second wedge inside the cooldown does not", S.restart(100000.0 + 60), False)
        report("no second call was made", len(calls), 1)
        report("after the cooldown it may again", S.restart(100000.0 + S.RESTART_COOLDOWN_S + 1), True)
    finally:
        subprocess.run, S.record, S.LAST_RESTART = real_run, real_rec, real_last
    print("test_stuck_scheduler_restart:", "FAILED" if FAILED else "ok")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
