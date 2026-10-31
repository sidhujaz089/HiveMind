#!/usr/bin/env python3
"""t415 oracle -- a suite killed at its cap must leave no process and no /tmp dir.

FAILS TODAY (fail-first). scripts/run_tests.py:196 runs each suite with plain
subprocess.run(timeout=) and no start_new_session, and its timeout handler
(scripts/run_tests.py:199-200) only reports -- so on timeout the runner kills
the suite's own process and nothing else: the children the suite spawned (a
fake model server, the coder.py it drove) are orphaned, and the suite's
finally: cleanup never runs on SIGKILL. That is the 2026-09-25 finding behind
t415: 15 orphaned /tmp/delivery-check-*/fake.py servers and 10 /tmp/coder-*
sandboxes.

What this proves, with a temp dir and a fake fixture only (no live state):
  1. run_tests._once reports the timeout exactly as it does today (rc -9,
     "TIMED OUT after" tail) -- the contract run_one() relies on is intact.
  2. whatever child process the fixture spawned is dead afterwards.
  3. the fixture's /tmp sandbox dir is gone afterwards.

The fixture mirrors the real leak shape: a throwaway 'suite' that creates a
/tmp/coder-* sandbox, spawns a long-lived child (a stand-in for fake.py) in
the same process group, then sleeps past the cap.
"""
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import run_tests  # noqa: E402

CAP = 2  # short cap, per t415 DONE WHEN ("sleeps past a short cap")

FIXTURE = """
import json, os, sys, tempfile, time

dest = os.environ["T415_MARKER"]
sandbox = os.path.join(tempfile.gettempdir(), "coder-t415-oracle-%d" % os.getpid())
os.makedirs(sandbox)
open(os.path.join(sandbox, "inside"), "w").write("leak")
# the fake model server, spawned the way the real suites spawn it:
# a child in this suite's own process group
import subprocess
server = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
with open(dest, "w") as f:
    json.dump({"server": server.pid, "sandbox": sandbox}, f)
time.sleep(120)
"""


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True   # exists, not ours -- still a leak


def main():
    work = Path(tempfile.mkdtemp(prefix="t415-oracle-work-"))
    failures = []
    marker = work / "marker.json"
    fixture = work / "test_t415_fixture_leak.py"
    fixture.write_text(FIXTURE)
    saved = dict(run_tests.TIMEOUT_FOR)
    saved_env = os.environ.pop("T415_MARKER", None)
    os.environ["T415_MARKER"] = str(marker)
    run_tests.TIMEOUT_FOR = {fixture.name: CAP}
    try:
        rc, secs, out = run_tests._once(fixture)
    finally:
        run_tests.TIMEOUT_FOR = saved
        if saved_env is None:
            os.environ.pop("T415_MARKER", None)
        else:
            os.environ["T415_MARKER"] = saved_env

    if rc != -9:
        failures.append(f"timeout not reported as rc -9 (got {rc}): {out!r}")
    if "TIMED OUT after" not in out:
        failures.append(f"timeout tail changed (got {out!r}) -- run_one's report relies on it")
    if not marker.exists():
        # the fixture never got to spawn anything: environment problem, say so
        print("  oracle could not verify the leak: fixture never ran "
              f"(rc={rc} out={out!r})")
        shutil.rmtree(work, ignore_errors=True)
        return 1
    info = json.loads(marker.read_text())
    time.sleep(0.5)                       # SIGKILL is async; give it a beat
    if _alive(info["server"]):
        failures.append(f"orphaned child still alive: pid {info['server']} "
                        "(python3 -c ...) -- this is the fake-server leak; "
                        f"kill {info['server']} to clean up")
    sb = Path(info["sandbox"])
    if sb.exists():
        failures.append(f"sandbox dir survived: {sb} -- rm -rf it to clean up")

    shutil.rmtree(work, ignore_errors=True)
    if failures:
        for f in failures:
            print("  FAIL", f)
        return 1
    print(f"  ok: cap kill left no process and no sandbox (rc={rc}, {secs}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
