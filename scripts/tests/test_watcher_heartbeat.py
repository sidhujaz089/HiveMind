#!/usr/bin/env python3
"""A dead watcher must read STALE, and a watcher's beat must say what it saw.

t278, the one with teeth. On 2026-09-11 two waiters polled a job that had died 90
minutes earlier and nothing said so, because a watcher publishes no beat at all:
silence is indistinguishable from "all is well." On 2026-09-13 a job finished at
12:14:38Z (agent-runs.jsonl) and its beat file still says running hours later.
This suite pins the three halves that were missing:

  1. a watcher's beat carries the observation as VALUES -- the watched job's name,
     that job's last-beat timestamp, and its age in seconds. Non-empty is not enough.
  2. kill the watcher while the job it watches is alive: status() reads STALE for
     the watcher and running for the job. The exact 131-minute failure, reproduced
     in one second by killing a self-spawned fake and aging its file with the file
     clock, which is what status() judges (heartbeat.py:72).
  3. a beat that names a watched job with no beat file is ORPHAN -- a finding, not
     a clean check.

Oracles never touch live state: every case points heartbeat.BEATS at a fresh temp
directory before anything is written, and the only process touched is one this
suite spawns and kills itself.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import heartbeat

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'ok ' if cond else 'BAD'}]  {name}"
          + ("" if cond else f"  -- {detail}"))
    if not cond:
        FAILS.append(name)


def run_case(name, fn):
    try:
        fn()
    except Exception as e:                       # recorded, never swallowed: the red
        check(name, False, f"{type(e).__name__}: {e}")   # reason is printed


def case_observation_carries_values():
    with tempfile.TemporaryDirectory(prefix="hb-watcher-") as td:
        heartbeat.BEATS = Path(td)
        heartbeat.beat("job-1", note="the job")
        p = heartbeat.BEATS / "job-1.json"
        old = time.time() - 1234
        os.utime(p, (old, old))
        job_last_beat = json.loads(p.read_text(encoding="utf-8"))["beat"]
        heartbeat.watch("waiter-1", "job-1")
        w = json.loads((heartbeat.BEATS / "waiter-1.json").read_text(encoding="utf-8"))
        check("the row records the watched job as a value", w.get("watched") == "job-1",
              json.dumps(w))
        check("the beat names the watched job", "job-1" in w.get("note", ""),
              w.get("note", ""))
        check("the beat carries the job's last-beat timestamp",
              job_last_beat in w.get("note", ""), w.get("note", ""))
        ages = [int(x) for x in re.findall(r"(\d+)\s*sec", w.get("note", ""))]
        check("the beat carries the job's age as '<n> sec'",
              any(1200 <= a <= 1300 for a in ages), w.get("note", ""))


def case_dead_watcher_is_stale():
    with tempfile.TemporaryDirectory(prefix="hb-watcher-") as td:
        heartbeat.BEATS = Path(td)
        heartbeat.beat("job-1", note="still running")
        code = (
            "import os, sys, time\n"
            "sys.path.insert(0, os.environ['HB_LIB'])\n"
            "import heartbeat\n"
            "from pathlib import Path\n"
            "heartbeat.BEATS = Path(os.environ['HBEATS'])\n"
            "heartbeat.watch('waiter-2', 'job-1')\n"
            "time.sleep(30)\n"
        )
        env = dict(os.environ, HB_LIB=str(Path(__file__).resolve().parents[1]),
                   HBEATS=str(heartbeat.BEATS))
        proc = subprocess.Popen([sys.executable, "-c", code], env=env)
        wp = heartbeat.BEATS / "waiter-2.json"
        for _ in range(50):                        # wait up to 5s for the live beat
            if wp.exists():
                break
            time.sleep(0.1)
        check("the live watcher published a beat", wp.exists())
        proc.kill()
        proc.wait(timeout=10)
        # The watcher is dead, so its beat file stops advancing. That is the
        # 131-minute gap, aged with the file clock rather than waited out:
        if wp.exists():
            old = time.time() - 1200
            os.utime(wp, (old, old))
        rows = {r["name"]: r for r in heartbeat.status()}
        check("a dead watcher reads STALE, not silence",
              rows.get("waiter-2", {}).get("state") == "STALE",
              str(rows.get("waiter-2")))
        check("the job it was watching still reads running",
              rows.get("job-1", {}).get("state") == "running",
              str(rows.get("job-1")))


def case_orphan_is_a_finding():
    with tempfile.TemporaryDirectory(prefix="hb-watcher-") as td:
        heartbeat.BEATS = Path(td)
        # job "ghost" never beat: there is no ghost.json
        heartbeat.watch("waiter-3", "ghost")
        rows = {r["name"]: r for r in heartbeat.status()}
        r = rows.get("waiter-3", {})
        check("a watcher naming a job with no beat file is a finding (ORPHAN)",
              r.get("state") == "ORPHAN", str(r))
        check("it is not swallowed as running or clean",
              r.get("state") not in ("running", "done", "STALE"), str(r))


def main():
    print("watcher heartbeat (t278): the dead watcher and the empty watch")
    run_case("a watcher's beat carries its observation as values",
             case_observation_carries_values)
    run_case("a dead watcher reads STALE while its job reads running",
             case_dead_watcher_is_stale)
    run_case("a watcher naming a job with no beat file is a finding",
             case_orphan_is_a_finding)
    if FAILS:
        print(f"FAIL: {len(FAILS)} case(s) red: {FAILS}")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
