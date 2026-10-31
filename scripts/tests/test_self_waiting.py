#!/usr/bin/env python3
"""A loop waiting for a pattern that matches itself must be detected.

`while pgrep -f foo.py >/dev/null; do sleep 30; done` is the obvious way to wait for a job and
it never exits: the waiter's own command line contains "foo.py", so pgrep matches the waiter.

Six instances on 2026-09-03, three alive at once for 2h30m, 2h19m and 1h37m. Nothing errored
and each looked exactly like a job still running, which is why none was noticed. The existing
check for this pattern only reads TRACKED shell files; these were typed straight into a shell
and never written to disk, so it could not see them. This one reads /proc.
"""
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
import importlib.util                                        # noqa: E402
spec = importlib.util.spec_from_file_location("css", REPO / "scripts" / "check_stale_services.py")
css = importlib.util.module_from_spec(spec)
spec.loader.exec_module(css)


def main():
    fails = []
    token = "zzz_selfwait_fixture_zzz.py"

    # THE REAL SHAPE: an inline `bash -c`, which is how every real instance was created.
    proc = subprocess.Popen(
        ["bash", "-c", f"while pgrep -f {token} >/dev/null; do sleep 2; done"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(2)
        hits = css.self_waiting_processes(min_age_s=0)
        caught = any(pid == proc.pid for pid, _m, _p in hits)
        print(f"  [{'ok' if caught else 'BAD'}]    a self-matching pgrep waiter is detected "
              f"(pid {proc.pid})")
        if not caught:
            fails.append(f"the detector missed a live self-waiter: {hits}")

        # THE AGE GATE: a command that merely QUOTES the pattern exits in seconds, and was a
        # false positive on this check's first run. Nothing young is reported.
        young = css.self_waiting_processes(min_age_s=180)
        quiet = not any(pid == proc.pid for pid, _m, _p in young)
        print(f"  [{'ok' if quiet else 'BAD'}]    a process younger than the threshold is not "
              f"reported")
        if not quiet:
            fails.append("the age gate does not gate")
    finally:
        proc.kill()
        proc.wait(timeout=10)

    # AND IT MUST NOT FIRE ON A HONEST WAITER. Waiting on a PID is the correct form.
    ok_proc = subprocess.Popen(
        ["bash", "-c", "P=999999; while kill -0 $P 2>/dev/null; do sleep 2; done"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        hits = css.self_waiting_processes(min_age_s=0)
        clean = not any(pid == ok_proc.pid for pid, _m, _p in hits)
        print(f"  [{'ok' if clean else 'BAD'}]    a PID-based waiter is NOT flagged")
        if not clean:
            fails.append("the correct way to wait was reported as a bug, which is how a "
                         "check gets silenced")
    finally:
        ok_proc.kill()
        ok_proc.wait(timeout=10)

    for x in fails:
        print("FAIL:", x)
    print("test_self_waiting:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
