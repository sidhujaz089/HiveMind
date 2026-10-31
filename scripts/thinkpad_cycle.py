#!/usr/bin/env python3
"""The ThinkPad eGPU cycle: Auditor until 01:00, then Frontline until nothing is ready to build, then the
Auditor again (Jas, 2026-10-14). The Manager stays on the GX10; the embedder lives on the ThinkPad CPU.

    python3 scripts/thinkpad_cycle.py status          # the mode, since when, and what would happen now
    python3 scripts/thinkpad_cycle.py step            # one decision (dispatch_loop's live tick runs it)
    python3 scripts/thinkpad_cycle.py set <auditor|frontline>   # switch now, by hand

WHY. The eGPU holds the Q4 (17.5 GB, Auditor: scoping + preflight) OR Gemma (Frontline: building), and
loading one while another is resident dipped the ThinkPad's RAM under the guard's 2 GB reserve
(2026-10-14 18:22:19Z). Jas: "Auditor on until 1 am, then ... frontline until it's done, then back to
auditor ... Then we don't need to worry about down time." Builds pile up by day, are built overnight.

HOW A SWITCH HAPPENS, over ticks, so nothing loads beside something else:
  1. DRAIN: the outgoing role is set to 0 lanes -- no new job starts on its model. Its running jobs
     finish; nothing is killed.
  2. UNLOAD: once no job of the old model runs, `ollama stop <model>` on the ThinkPad.
  3. ON: the incoming role gets its lanes back (the number it had when last switched off); its model
     loads with the first job, into an eGPU nothing else is loading into.
The lanes each role had are remembered in the state file, so a Models-tab change to them survives.
OFF SWITCH: workspace/run-logs/thinkpad-cycle-off (any content) -- step() then does nothing.
Every decision is a row in workspace/thinkpad-cycle.jsonl.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
STATE = REPO / "workspace" / "run-logs" / "thinkpad-cycle.json"
LEDGER = REPO / "workspace" / "thinkpad-cycle.jsonl"
OFF = REPO / "workspace" / "run-logs" / "thinkpad-cycle-off"
TICK = REPO / "workspace" / "run-logs" / "dispatch-tick-latest.json"
SWITCH_HOUR = 1                       # local time: the Auditor hands over at 01:00
NIGHT_END_H = 6                       # ... any time in the six hours after it, if builds are ready
MODES = {"auditor": "auditor", "frontline": "coder"}     # mode -> role key in ROLES.json
DEFAULT_LANES = {"auditor": 4, "coder": 6}


def _now():
    return time.time()


def _iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_now() if t is None else t))


def load(path=None):
    try:
        return json.loads(Path(path or STATE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as e:
        print(f"  NOTE: {STATE.name} unreadable ({e}); treated as no state", file=sys.stderr)
        return {}


def save(st, path=None):
    p = Path(path or STATE)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1) + "\n", encoding="utf-8")
    tmp.replace(p)


def log(row, path=None):
    with Path(path or LEDGER).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": _iso(), **row}) + "\n")


def last_switch_hour(now=None):
    """Epoch of the most recent local SWITCH_HOUR:00 at or before now."""
    now = _now() if now is None else now
    lt = time.localtime(now)
    t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, SWITCH_HOUR, 0, 0, 0, 0, -1))
    return t if t <= now else t - 86400


def ready_to_build(tick=None):
    """Builds ready and waiting for a lane, from the scheduler's last tick (no suite is run here)."""
    try:
        d = json.loads(Path(tick or TICK).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"  NOTE: last tick unreadable ({e}); ready count unknown", file=sys.stderr)
        return None
    n = 0
    for x in d.get("skipped") or []:
        why = x.get("why") if isinstance(x, dict) else x[1]
        # A RED-ON-MAIN HOLD IS NOT "NOTHING TO BUILD": the builds are still there, held for at most
        # red_on_main.WINDOW_S; counting them keeps Frontline on the eGPU through the hold.
        # "waiting for a free lane" is the same skip since t9537 (it names the role now).
        n += str(why).startswith(("waiting -- every model has a lane", "waiting for a free lane",
                                  "no free sequence slot",
                                  "HELD: red on main"))
    return n


def jobs_on(model):
    """Live jobs whose command line runs on `model` (scoping_lanes.lane_model)."""
    import lane_status
    import scoping_lanes
    return sum(1 for L in lane_status.lanes() if scoping_lanes.lane_model(L.get("argv") or []) == model)


def role_model(role):
    import role_store
    return role_store.load()["roles"][role].get("model")


def set_lanes(role, n, why):
    import role_store
    role_store.update_role(role, "thinkpad-cycle", f"lanes -> {n} ({why})", lanes=n)


def unload(model):
    import thinkpad_guard
    _url, ssh = thinkpad_guard.device()
    return thinkpad_guard.unload(ssh, model)


def decide(st, now, ready, running_old):
    """Pure. (action, detail): 'start', 'drain', 'unload', 'on' or 'stay'."""
    mode, phase = st.get("mode"), st.get("phase", "on")
    if not mode:
        return "start", "auditor"
    if phase == "draining":
        return ("stay", f"waiting for {running_old} {st.get('from')} job(s) to finish") if running_old \
            else ("unload", st.get("from"))
    if phase == "unloaded":
        return "on", mode
    # THE NIGHT WINDOW: the handover happens between SWITCH_HOUR and NIGHT_END_H hours after it. With
    # nothing ready at 01:00 the Auditor keeps the eGPU until the NEXT night, not until the first build
    # that turns up in the afternoon.
    t1 = last_switch_hour(now)
    if mode == "auditor" and st.get("since", now) < t1 and now - t1 < NIGHT_END_H * 3600 and (ready or 0) > 0:
        return "drain", "frontline"
    if mode == "frontline" and ready == 0 and running_old == 0:
        return "drain", "auditor"
    return "stay", ""


def step(now=None):
    if OFF.exists():
        return "off"
    now = _now() if now is None else now
    st = load()
    lanes = st.get("lanes") or dict(DEFAULT_LANES)
    # A MODELS-TAB CHANGE WINS (2026-10-14: Jas set Frontline 0 and the Auditor 1 by hand while the cycle
    # was in frontline mode). A role switched on by hand is adopted as the mode, and any role's lanes
    # above 0 become the lanes it gets back -- the cycle never restores an old number over his.
    if st.get("mode") and st.get("phase") == "on":
        import role_store
        roles = role_store.load()["roles"]
        on = {m for m, r in MODES.items() if (roles[r].get("lanes") or 0) > 0}
        for m, r in MODES.items():
            if (roles[r].get("lanes") or 0) > 0:
                lanes[r] = roles[r]["lanes"]
        if len(on) == 1 and st["mode"] not in on:
            st = {"mode": on.pop(), "phase": "on", "since": now, "lanes": lanes}
            save(st)
            log({"action": "adopt", "detail": st["mode"], "mode": st["mode"], "phase": "on",
                 "note": "set by hand on the Models tab"})
            return f"adopt {st['mode']} (set by hand)"
        st["lanes"] = lanes
    old_role = MODES.get(st.get("from") or st.get("mode") or "", "")
    running_old = jobs_on(role_model(old_role)) if old_role else 0
    action, detail = decide(st, now, ready_to_build(), running_old)
    if action == "stay":
        return f"stay ({st.get('mode')}{'; ' + detail if detail else ''})"
    if action == "start":
        # ADOPT WHAT IS RUNNING: Frontline building with the Auditor off is frontline mode; anything
        # else starts as the Auditor, with Frontline off.
        import role_store
        roles = role_store.load()["roles"]
        mode = "frontline" if (roles["coder"].get("lanes") or 0) > 0 and not (roles["auditor"].get("lanes") or 0) \
            else "auditor"
        if mode == "auditor":
            set_lanes("coder", 0, "thinkpad-cycle starts in auditor mode")
            set_lanes("auditor", lanes["auditor"], "thinkpad-cycle starts in auditor mode")
        st = {"mode": mode, "phase": "on", "since": now, "lanes": lanes}
        detail = mode
    elif action == "drain":
        out_role = MODES[st["mode"]]
        import role_store
        lanes[out_role] = role_store.load()["roles"][out_role].get("lanes") or lanes[out_role]
        set_lanes(out_role, 0, f"thinkpad-cycle: draining {st['mode']} for {detail}")
        st = {"mode": detail, "from": st["mode"], "phase": "draining", "since": now, "lanes": lanes}
    elif action == "unload":
        model = role_model(MODES[detail])
        ok = unload(model)
        st = {**st, "phase": "unloaded", "unloaded": model, "unload_ok": ok}
    elif action == "on":
        role = MODES[st["mode"]]
        set_lanes(role, lanes[role], f"thinkpad-cycle: {st['mode']} on")
        st = {"mode": st["mode"], "phase": "on", "since": now, "lanes": lanes}
    save(st)
    log({"action": action, "detail": detail, "mode": st.get("mode"), "phase": st.get("phase"),
         "ready_to_build": ready_to_build(), "running_old": running_old})
    return f"{action} {detail}"


def main(argv):
    if argv[:1] == ["step"]:
        print(f"  thinkpad-cycle: {step()}")
        return 0
    if argv[:1] == ["status"]:
        st = load()
        print(json.dumps({**st, "ready_to_build": ready_to_build(), "off": OFF.exists(),
                          "last_switch_hour": _iso(last_switch_hour())}, indent=1))
        return 0
    if argv[:1] == ["set"] and len(argv) == 2 and argv[1] in MODES:
        st = load()
        if st.get("mode") != argv[1]:
            lanes = st.get("lanes") or dict(DEFAULT_LANES)
            if st.get("mode"):
                set_lanes(MODES[st["mode"]], 0, f"thinkpad-cycle set {argv[1]} by hand: draining")
            save({"mode": argv[1], "from": st.get("mode") or ("frontline" if argv[1] == "auditor" else "auditor"),
                  "phase": "draining", "since": _now(), "lanes": lanes})
            log({"action": "set", "detail": argv[1]})
        print(f"  thinkpad-cycle: {argv[1]} (switching over the next ticks)")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
