#!/usr/bin/env python3
"""Why is this lane idle? (harness #4, plan 2026-10-19) -- one line per model role with a free lane,
naming what holds it.

    python3 scripts/lane_idle.py          # the lines
    python3 scripts/lane_idle.py --json

Every idle lane on the weekend of 10-16..19 cost an investigation: lanes zeroed by a waiting build,
a build lane offered without memory, the tick lock, Swift down or killed by the memory guard, a
role's lane cap. Each answer was already written somewhere -- a pause flag, the memory guard's
ledger, the dispatcher's last tick -- and nothing put them side by side. This does, in the order a
person would check them; the first that applies is the reason:

  1. the scheduler is paused                 workspace/run-logs/scheduler-paused.enabled
  2. the role's device is paused              device-paused-<device>.enabled (dispatch_loop.DEVICE_OF)
  3. its model server does not answer         dispatch_loop.unready_models
  4. the memory guard stopped work recently   workspace/memory-guard.jsonl, last 30 min
  5. the dispatcher has not ticked            the cron log's age (the tick lock, or cron itself)
  6. the last tick queued its work, and why   the tick's "queued ... <model>: <reason>" lines
  7. nothing was waiting for it               said as that, not as a fault
"""
import calendar
import json
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
TICK_LOG = REPO / "workspace" / "run-logs" / "dispatch-loop-cron.log"
GUARD = REPO / "workspace" / "memory-guard.jsonl"
GUARD_WINDOW_S = 30 * 60
TICK_STALE_S = 6 * 60          # cron ticks every 2 min
TICK_TAIL_BYTES = 400_000      # where the last tick lives; read from the end, the log is huge


def last_tick(path=TICK_LOG):
    """The lines of the newest complete tick block in the cron log (from its last 'inference path'
    or WAIT/PAUSED/REFUSED line to the end)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - TICK_TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    starts = [i for i, ln in enumerate(lines)
              if re.match(r"\s+(inference path:|WAIT --|PAUSED --|REFUSED --)", ln)]
    return lines[starts[-1]:] if starts else []


def _guard_recent(now, path=GUARD):
    try:
        rows = [json.loads(ln) for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    except (OSError, ValueError):
        return None
    for r in reversed(rows):
        try:
            at = calendar.timegm(time.strptime(r["at"], "%Y-%m-%dT%H:%M:%SZ"))
        except (KeyError, ValueError) as e:
            print(f"  NOTE: memory-guard row without a readable 'at' skipped ({type(e).__name__}: {e}): {r}",
                  file=sys.stderr)
            continue
        if now - at <= GUARD_WINDOW_S and r.get("stopped"):
            return r
        break
    return None


def reasons(now=None, d=None, running=None, tick=None, tick_age_s=None, paused=None, devices=None,
            down=None, guard=None):
    """[{role, model, lanes, running, reason}] for each model role with a free lane. Every input can
    be passed in (tests); left out, it is read live."""
    import task_routing
    import dispatch_loop as DL
    now = time.time() if now is None else now
    d = d or task_routing.load()
    if running is None:
        import lane_status
        running = [r.get("role") for r in lane_status.snapshot()["lanes"]]
    if tick is None:
        tick = last_tick()
    if tick_age_s is None:
        try:
            tick_age_s = now - TICK_LOG.stat().st_mtime
        except OSError:
            tick_age_s = None
    if paused is None:
        import scheduler_commands
        paused = scheduler_commands.is_paused()
        devices = scheduler_commands.paused_devices() if devices is None else devices
    devices = devices or {}
    guard = _guard_recent(now) if guard is None else guard
    out = []
    for k in task_routing.model_roles(d):
        r = d["roles"][k]
        if not task_routing.tasks_of(d, k):
            continue
        lanes = task_routing.role_lanes(d, k)
        if lanes == 0:
            continue                                       # 0 lanes = off, by Jas's choice
        cap = lanes if lanes is not None else 1
        busy = sum(1 for x in running if x == k)
        if busy >= cap:
            continue
        model = r.get("model") or ""
        dev = DL.DEVICE_OF.get(model, "gx10")
        if paused:
            why = "the scheduler is paused -- Resume on the Hub"
        elif dev in devices:
            n = devices[dev]
            why = f"its device {dev} is paused ({n.get('who')}: {n.get('why')}) -- Resume on the Hub"
        elif (down if down is not None else DL.unready_models([model])).get(model):
            why = f"its model server does not answer ({(down or DL.unready_models([model]))[model]})"
        elif guard:
            why = (f"the memory guard stopped {guard.get('stopped')} at {guard.get('at')} "
                   f"({guard.get('available_gb')} GB free)")
        elif tick_age_s is not None and tick_age_s > TICK_STALE_S:
            why = (f"the dispatcher has not ticked for {tick_age_s / 60:.0f} min -- a held tick lock "
                   f"(/tmp/dispatch_loop.lock) or cron itself")
        else:
            q = [ln.split(": ", 1)[1] for ln in tick
                 if re.match(r"\s+queued\s", ln) and ": " in ln and model in ln]
            nob = next((ln.strip() for ln in tick if "no build lane this tick" in ln), None)
            if q:
                why = f"its work is queued: {q[-1]}"
            elif nob and any(t.get("task") == "building" for t in task_routing.tasks_of(d, k)):
                why = nob
            else:
                why = "nothing was waiting for it in the last tick"
        out.append({"role": k, "name": r.get("name", k), "model": model, "lanes": lanes,
                    "running": busy, "reason": why})
    return out


def main(argv):
    rows = reasons()
    if "--json" in argv:
        print(json.dumps(rows, indent=1))
        return 0
    if not rows:
        print("  every lane with work to do is working")
    for r in rows:
        cap = "no limit" if r["lanes"] is None else r["lanes"]
        print(f"  {r['name']:12} {r['running']}/{cap} lanes  -- {r['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
