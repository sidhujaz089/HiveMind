#!/usr/bin/env python3
"""Is a job waiting on a model that will never load? The wedged-scheduler check (t314).

    python3 scripts/stuck_scheduler.py            # check the running jobs now; record any wedge
    python3 scripts/stuck_scheduler.py --dry-run  # say, record nothing

WHY (t314, observed 2026-09-14 22:40-22:54). After 15 evictions in two days, every request for
qwen3.8:27b-mtp-bf16 hung -- the supervisor's for 14+ minutes, a 1-token /api/generate for the
full 90 s -- with NO load attempt and nothing in ollama's journal, while resident gemma4:26b
answered in seconds. inference_health.probe() said UP because it tests the RESIDENT model, so
every standing check missed it. Only a restart of the server cleared it.

THE RULE (decided): stuck = the job has waited PAST grace, AND its model is not resident (full tag,
so qwen3.8:27b-mtp does not stand in for qwen3.8:27b-mtp-bf16), AND no load of it has been logged,
AND no known holder has the slot (the BUSY shape: a legitimate queue). Any one of those false and
it is loading, running or queued -- not wedged.

A wedge is RECORDED to workspace/run-logs/ollama-events.jsonl with status 'stuck-scheduler' (never
'ok'), which error_scan surfaces and session start prints.

AND NOW IT RESTARTS (Jas, 2026-10-01: "I agree to give Ollama super user access"). A sudoers rule
(/etc/sudoers.d/ollama-restart) allows exactly `systemctl restart ollama` without a password. On a
wedge the server is restarted -- at most once per RESTART_COOLDOWN_S, so a wedge that a restart
does not clear is reported, not looped on -- and the restart is recorded beside the wedge.
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EVENTS = REPO / "workspace" / "run-logs" / "ollama-events.jsonl"
RESTART_COOLDOWN_S = 1800       # one restart per 30 min; a second wedge inside that is only reported
LAST_RESTART = REPO / "workspace" / "run-logs" / "ollama-restart.last"
DEFAULT_GRACE_S = 300.0          # a cold load of the largest model here takes ~2-3 min
OLLAMA = "http://127.0.0.1:11434"


def decided(model, job_age_s, resident, load_logged, holder_known, grace_s=DEFAULT_GRACE_S):
    """True when the job is waiting on a wedged scheduler (the four conjuncts above)."""
    return (job_age_s > grace_s and model not in set(resident or [])
            and not load_logged and not holder_known)


def event_row(model, job_age_s, grace_s=DEFAULT_GRACE_S):
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "at_epoch": time.time(),
            "status": "stuck-scheduler", "model": model, "job_age_s": round(float(job_age_s), 1),
            "grace_s": float(grace_s),
            "say": (f"A job has waited {job_age_s:.0f}s for {model}, which is not resident and has no load "
                    f"logged, and nothing known holds the slot. The 2026-09-14 wedge looked like this; only "
                    f"a restart of the Ollama server cleared it. Restart it if the job is still waiting.")}


def record(row, path=None):
    p = Path(path or EVENTS)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def resident_models(url=OLLAMA):
    with urllib.request.urlopen(f"{url}/api/ps", timeout=10) as f:
        return [m["name"] for m in json.load(f).get("models", [])]


def load_logged_since(model, since_epoch, events=None):
    """Did ollama log a load of this model since the job started? (ollama_events.py rows)."""
    p = Path(events or EVENTS)
    if not p.is_file():
        return False
    torn = 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            torn += 1                                           # a torn line is not a load
            r = {}
        if (r.get("at_epoch") or 0) >= since_epoch and model in json.dumps(r) and "load" in json.dumps(r).lower():
            return True
    if torn:
        print(f"  NOTE: {torn} unreadable line(s) in {p} were not read as loads", file=sys.stderr)
    return False


def model_wait(job, now, history=None):
    """Seconds a job's model request has been waiting, from its log; None if none is in flight.

    coder.py prints "  attempt N … " and finishes that line only when the model answers, so a last
    line that is an unfinished attempt is a request in flight, waiting since the log last changed.
    A job with no log is judged as before (from its start), so nothing becomes un-checkable."""
    import dispatch_loop
    rows = history if history is not None else dispatch_loop.rows()
    log = next((r.get("log") for r in rows if r.get("event") == "started" and r.get("pid") == job.get("pid")), None)
    if not log:
        return None if job.get("kind") == "production" else float("inf")
    p = REPO / log
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        mtime = p.stat().st_mtime
    except OSError:
        return None
    if job.get("kind") != "production":
        return now - mtime                          # a review's log stays quiet while it waits
    last = text.rstrip("\n").rsplit("\n", 1)[-1]
    if "attempt" in last and last.rstrip().endswith("…"):
        return now - mtime
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    sys.path.insert(0, str(REPO / "scripts"))
    import dispatch_loop
    import modellease
    try:
        resident = resident_models()
    except Exception as e:                                          # noqa: BLE001 -- said, then stop
        print(f"  cannot read the server's resident models ({type(e).__name__}: {e}); nothing decided")
        return 2
    now = time.time()
    found = 0
    history = dispatch_loop.rows()
    # ledger_running() keeps only kind/key/model/pid/port; the start time is on the job's own
    # "started" row, found by its pid.
    started_at = {r.get("pid"): r.get("at") for r in history if r.get("event") == "started"}
    running = dispatch_loop.ledger_running(history)
    for job in running:
        model = job.get("model") or ""
        try:
            import calendar
            started = calendar.timegm(time.strptime(started_at[job.get("pid")], "%Y-%m-%dT%H:%M:%SZ"))
        except (KeyError, TypeError, ValueError):
            print(f"  job {job.get('key')}: no start time, not judged")
            continue
        age = now - started
        # WAITING ON THE MODEL, NOT MERELY RUNNING (2026-10-01). A build spends its first minutes
        # baselining the sandbox before it asks the model anything; at 6 min p234's Gemma was not
        # resident because nobody had asked yet, this called it wedged, restarted Ollama, and
        # killed the build and two RCAs. A job whose log shows no model request in flight is not
        # judged; one that has, is judged on how long that request has been waiting.
        waiting = model_wait(job, now)
        if waiting is None:
            print(f"  job {job.get('key') or job.get('chunk')}: no model request in flight yet -- not judged")
            continue
        age = waiting
        lease = modellease.held(modellease.lease_for(model))
        holder_known = bool(lease and str(job.get("pid")) not in str(lease.get("holder", "")))
        if decided(model, age, resident, load_logged_since(model, started), holder_known):
            found += 1
            row = event_row(model, age)
            print(f"  STUCK  {job.get('kind')} {job.get('key')} on {model}: {row['say']}")
            if not a.dry_run:
                record(row)
    print(f"  {found} wedged job(s) among {len(running)} running")
    if found and not a.dry_run:
        restart(now)
    return 1 if found else 0


def restart(now):
    """Restart Ollama through the one sudoers rule, unless one already ran in the cooldown."""
    import subprocess
    try:
        last = float(LAST_RESTART.read_text().strip())
    except (OSError, ValueError):
        last = 0.0
    if now - last < RESTART_COOLDOWN_S:
        print(f"  restart NOT done: the last one was {int(now - last)} s ago (cooldown "
              f"{RESTART_COOLDOWN_S} s) -- a wedge a restart did not clear needs a person")
        return False
    r = subprocess.run(["sudo", "-n", "/usr/bin/systemctl", "restart", "ollama"],
                       capture_output=True, text=True, timeout=120)
    LAST_RESTART.parent.mkdir(parents=True, exist_ok=True)
    LAST_RESTART.write_text(str(now))
    ok = r.returncode == 0
    record({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)), "source": "stuck_scheduler",
            "status": "restarted" if ok else "restart-failed",
            "say": ("restarted the Ollama server to clear a wedged scheduler" if ok else
                    f"restart refused: {(r.stderr or r.stdout).strip()}")})
    print(f"  {'RESTARTED ollama' if ok else 'restart FAILED: ' + (r.stderr or r.stdout).strip()}")
    return ok


if __name__ == "__main__":
    sys.exit(main())
