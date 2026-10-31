#!/usr/bin/env python3
"""Control primitives for scripts/dispatch_loop.py, behind the Hub's Status-tab buttons.

Implemented for chunk p32. The behaviour is specified by scripts/tests/test_scheduler_commands.py
and by workspace/briefs/brief-p32-scheduler-commands.txt, and is written by the coder
dispatch (chunk p32). This file exists first because preflight_brief.py refuses to review a
brief that names a file which does not exist -- "create it first as its own step" -- and
because coder.py's shape is "make this existing suite pass", not "author a new module".

Jas, 2026-09-15, asked for Status-tab buttons: pause scheduler, stop job, move to front,
unpark, run tick, wedge toggle, ollama restart. Three of them are here. "unpark" belongs to
scripts/supervise.py's PARK ledger; "run tick" and "wedge toggle" reuse mechanisms that
already exist and need only a button.
"""
import json
import os
import signal
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The pause signal is a FILE, for the same reason the kill switch and the wedge flag are:
# a file is the one signal that still works when everything else is wedged.
PAUSE_FLAG = REPO / "workspace" / "run-logs" / "scheduler-paused.enabled"

# Device-pause ledger: a bare flag file cannot answer who paused a device, who cleared it,
# when, from which pid, and why; this append-only record pins that at the primitive.
DEVICE_PAUSE_LEDGER = REPO / "workspace" / "run-logs" / "device-pause.jsonl"
device_pause_seq = 0


# TESTS MUST NOT WRITE HERE (handover 2026-10-23 item 5; 2026-10-23 measurement). The ledger
# is append-only evidence of a real operational fact -- the 2026-10-21 "I paused the ThinkPad,
# why did it start on its own?" reading depends on it. Counting the live file's own
# source_argv0 field: 648 rows from scripts/tests/test_device_pause.py, 192 from
# test_scheduler_breaker.py, 76 from test_scheduler_commands.py -- 916 fake rows a reader
# cannot tell from Jas's real pauses. The suites redirected PAUSE_FLAG (which chooses where
# the FLAGS live) but not DEVICE_PAUSE_LEDGER (read at call time, not derived from the flag),
# so every pause_device/resume_device/boot in a suite appended to the live record.
# The guard RAISES on the live path -- a suite that forgets to redirect fails loudly at the
# primitive instead of quietly writing, the shape test_console_run_command_guard.py established.
# The guard refuses a TEST process that targets the live path and forgets to redirect. It
# must NOT refuse production: the memory guard (argv0 scripts/memory_guard.py), the Hub
# (projects/app-repo/feedback.py) and scheduler_commands.py boot ARE the ledger's writers.
def _looks_like_a_test(argv0):
    a = (argv0 or "").replace(os.sep, "/")
    return "scripts/tests/" in a or Path(a).name.startswith("test_")


def refuse_live_device_pause_ledger(path):
    """Refuse to record a device pause into the live ledger FROM A TEST. Returns the path when safe.

    Production keeps writing to the live path -- that is the ledger's job. What is refused is a
    process whose own argv[0] names a file under scripts/tests/ (or test_*.py) targeting it:
    the same self-attestation the ledger's source_argv0 field already records, read at the
    moment of the write instead of discovered in a grep months later. Paths compare resolved,
    so a symlinked spelling of the live path is the live path.
    """
    p = Path(path)
    live = REPO / "workspace" / "run-logs" / "device-pause.jsonl"
    try:
        targets_live = p.resolve() == live.resolve()
    except OSError:
        targets_live = p == live
    if targets_live and _looks_like_a_test(sys.argv[0] if sys.argv else ""):
        raise RuntimeError(
            "refusing to write the LIVE device-pause ledger from a test: "
            f"{live}\nA test must point DEVICE_PAUSE_LEDGER at a temp path (its PAUSE_FLAG "
            "redirect does not move the ledger: the constant is read at call time).")
    return p


def _owner_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError, TypeError, OSError):
        return True                      # cannot tell -- keep the pause
    return True


def is_paused():
    """True while the flag exists -- unless it is an OWNED pause whose owner has died.

    A pause taken with owner_pid (session_end's test sweep) is released by the owner's finally,
    and a finally does not run when the process is killed. 2026-10-12: a session_end killed
    mid-sweep left the line paused from 14:58Z, and nothing dispatched for three hours (the same
    shape as 2026-09-15's five). A pause by a person or the memory guard carries no pid and is
    never released here."""
    if not PAUSE_FLAG.exists():
        return False
    try:
        pid = json.loads(PAUSE_FLAG.read_text(encoding="utf-8")).get("owner_pid")
    except (OSError, ValueError, AttributeError):
        return True
    if pid is not None and not _owner_alive(pid):
        PAUSE_FLAG.unlink(missing_ok=True)   # a concurrent reader may have released it first
        return False
    return True


def pause(who, why, owner_pid=None):
    PAUSE_FLAG.parent.mkdir(parents=True, exist_ok=True)
    note = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "who": who, "why": why}
    if owner_pid is not None:
        note["owner_pid"] = int(owner_pid)
    PAUSE_FLAG.write_text(json.dumps(note) + "\n", encoding="utf-8")


def resume():
    # Resuming an already-unpaused scheduler is a no-op, not an error.
    if PAUSE_FLAG.exists():
        PAUSE_FLAG.unlink()


def stop_job(pid, who="jas"):
    # Neither source alone is complete: lane_status sees the production lanes, the ledger
    # sees everything this scheduler started (preflight, console, priority work).
    import dispatch_loop as DL
    import lane_status
    tracked = {int(r["pid"]) for r in lane_status.snapshot()["lanes"] if r.get("pid")}
    tracked |= {int(r["pid"]) for r in DL.ledger_running() if r.get("pid")}
    # A SCOPING WORKER (parallel scoping, 2026-10-08) is a scoping_queue.sh subshell; its claim
    # holds the pid. Stop takes the worker's children too (ask_q16 under timeout), by pid.
    import scoping_lanes
    workers = {int(p) for _, p in scoping_lanes.live()}
    if int(pid) in workers:
        def tree(p):
            kids = []
            for f in Path("/proc").glob("[0-9]*/stat"):
                try:
                    if int(f.read_text().rsplit(")", 1)[1].split()[1]) == p:
                        kids.append(int(f.parent.name))
                except (OSError, ValueError, IndexError):
                    kids.append(None)       # a process that exited mid-walk: dropped below
            return [p] + [d for k in kids if k is not None for d in tree(k)]
        gone = 0
        for q in reversed(tree(int(pid))):
            try:
                os.kill(q, signal.SIGTERM)
            except ProcessLookupError:
                gone += 1                   # exited on its own before the signal reached it
        return True, (f"SIGTERM sent to scoping worker {pid} and its children (by {who})"
                      + (f"; {gone} had already exited" if gone else ""))
    if int(pid) not in tracked:
        return False, f"pid {pid} is not tracked by dispatch_loop -- nothing stopped"
    # No try/except: a kill that raises did not happen, and must not be reported as one.
    os.kill(int(pid), signal.SIGTERM)
    return True, f"SIGTERM sent to pid {pid} (by {who})"


def promote(chunk_name, loop_path=None):
    import parallel_dispatch as pd
    loop_path = Path(loop_path) if loop_path is not None else REPO / "scripts" / "autonomy_loop.sh"
    if not loop_path.is_file():
        return False, f"loop table {loop_path} does not exist -- nothing promoted"
    lines = loop_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    first_row = next((i for i, l in enumerate(lines) if pd.CHUNK_ROW.match(l)), None)
    if first_row is None:
        return False, f"no chunk rows in {loop_path} -- nothing to promote to the front of"
    target = next((i for i, l in enumerate(lines)
                   if pd.CHUNK_ROW.match(l) and l.split("|", 1)[0].strip() == chunk_name), None)
    if target is None:
        return False, f"chunk {chunk_name} is not in {loop_path} -- nothing promoted"
    lines.insert(first_row, lines.pop(target))
    loop_path.write_text("".join(lines), encoding="utf-8")
    return True, f"moved {chunk_name} to the front of {loop_path.name}"


# THE MASTER BREAKER (Jas, 2026-10-06): "that button is the key that fires and stops everything."
# The Hub's pause/resume buttons call these, not pause()/resume() alone: the flag stops new work,
# and production.target (Swift + the Manager queue, deploy/systemd/) is stopped or started with it.
# session_end's test-sweep pause deliberately uses pause() only -- a sweep must not unload Swift.
# At boot machine-key.service runs `scheduler_commands.py boot`: paused stays dark, else it fires.
PRODUCTION_TARGET = "production.target"


import subprocess as _subprocess
_REAL_RUN = _subprocess.run


# A TEST NEVER DRIVES THE LIVE UNITS (t1557, 2026-10-23). test_device_pause.py faked _systemctl, but a
# person's gx10_off stops the production units through its own subprocess.run (b9cccaaca), which it
# did not fake -- so every run of that suite, directly or inside a coder.py sandbox baseline, ran
# `systemctl --user --no-block stop scoping-queue@manager.service scoping-queue@auditor.service` on the live box.
# The hourly sweep did it at 13:24, 14:22, 14:26, 14:29 and 14:34 MDT and the Manager and Auditor never
# stayed up. Same shape as refuse_live_device_pause_ledger: a test process reaching the REAL
# systemctl raises here, at the primitive. A suite that fakes subprocess.run is hermetic and passes.
def refuse_live_systemctl_from_test(argv):
    if _subprocess.run is _REAL_RUN and _looks_like_a_test(sys.argv[0] if sys.argv else ""):
        raise RuntimeError(
            f"refusing to run the REAL systemctl from a test: {' '.join(argv)}\n"
            "A test must fake subprocess.run (or scheduler_commands._systemctl) before it pauses or "
            "resumes: these calls stop and start the live production units.")


def _systemctl(*args):
    argv = ["systemctl", "--user", "--no-block", *args, PRODUCTION_TARGET]
    refuse_live_systemctl_from_test(argv)
    # --no-block: stopping waits on a docker rm and starting on a model load; the Hub must not.
    return _subprocess.run(argv, capture_output=True, text=True, timeout=60)


def breaker_off(who, why):
    pause(who, why)
    r = _systemctl("stop")
    return r.returncode == 0, (r.stderr or "").strip()


# A DEVICE PAUSE (Jas, 2026-10-13: "I don't want the memory guard on the GX10 to also stop the
# ThinkPad. They should have independent memory guards"). The memory guard used breaker_off, the
# master key, so a GX10 memory trip stopped the ThinkPad's work too -- whose memory is its own 24 GB.
# Now it pauses ONE device: the tick skips jobs whose model lives there (dispatch_loop.DEVICE_OF)
# and starts the rest. The master key still stops everything; Resume clears both.
def _device_flag(device):
    return PAUSE_FLAG.parent / f"device-paused-{device}.enabled"


def _record_device_pause(device, action, who, why, flag_id, pause_who=None):
    """Append one device-pause acquire/clear record at the primitive that causes it.

    Refuses (loudly) to append to the LIVE ledger from a test process -- see
    refuse_live_device_pause_ledger (t-3b). A suite that forgets to redirect fails here
    with the remedy in its message, instead of writing fake rows a reader can't tell from
    Jas's real pauses (916 of them were in the live file when this shipped)."""
    refuse_live_device_pause_ledger(DEVICE_PAUSE_LEDGER)
    DEVICE_PAUSE_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "device": device,
           "action": action,
           "who": who,
           "why": why,
           "source_pid": os.getpid(),
           "source_argv0": sys.argv[0] if sys.argv and sys.argv[0] else "",
           "epoch": time.time(),
           "flag_id": flag_id}
    if pause_who is not None:
        row["pause_who"] = pause_who
    with open(DEVICE_PAUSE_LEDGER, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def pause_device(device, who, why):
    global device_pause_seq
    device_pause_seq += 1
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    flag_id = f"{os.getpid()}-{at}-{device_pause_seq}"
    f = _device_flag(device)
    f.parent.mkdir(parents=True, exist_ok=True)
    # A PAUSE IS NEVER OVERWRITTEN BY SOMEONE ELSE'S (Jas, 2026-10-21: "I paused the thinkpad. Why did it
    # start on its own?"). His Hub pause at 02:25Z was rewritten by thinkpad_guard's at 05:13Z, and at
    # 05:23Z the guard resumed "its own" pause. The first holder keeps the device; a second pause is
    # recorded and dropped, so no guard can later clear a pause it did not take. Resume still clears all.
    if f.exists():
        try:
            held = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            held = {"who": "?", "flag_id": "unreadable"}
        if held.get("who") != who:
            _record_device_pause(device, "already-paused", who, why, held.get("flag_id") or "legacy",
                                 pause_who=held.get("who"))
            return
    f.write_text(json.dumps({"at": at, "who": who,
                             "why": why, "device": device, "flag_id": flag_id}) + "\n", encoding="utf-8")
    _record_device_pause(device, "pause", who, why, flag_id)


# WHO STOPPED WORK MID-RUN (Jas, 2026-10-20: sweep on Resume only "if the memory guards were triggered
# ... a reboot should count too"). A pause of his own, or any other, resumes without a sweep.
INTERRUPTIONS = ("memory-guard", "thinkpad-guard", "boot")


def sweep_if_interrupted(whos):
    """Run the temp sweeper when any cleared pause was an interruption. -> rows removed ([] if none)."""
    if not any(w in INTERRUPTIONS for w in whos):
        return []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import sweep
        rows = sweep.sweep_after_interruption()
        print(f"resume: {len(rows)} leftover temp dir(s) swept after a {', '.join(sorted(set(whos) & set(INTERRUPTIONS)))} pause",
              flush=True)
        return rows
    except Exception as e:                                          # noqa: BLE001 -- said; resume still goes on
        print(f"resume: temp sweep failed ({type(e).__name__}: {e}); production resumes anyway", file=sys.stderr)
        return []


def resume_device(device, who="unspecified", why="", sweep=True):
    """Clear a device's pause. -> who had paused it (None if it was not paused). sweep=False when the
    caller sweeps once itself (gx10_on, breaker_on)."""
    flag = _device_flag(device)
    if not flag.exists():
        return None
    try:
        note = json.loads(flag.read_text(encoding="utf-8"))
        pause_who = note.get("who")
        flag_id = note.get("flag_id") or "legacy"
    except (OSError, ValueError):
        pause_who = "?"
        flag_id = "unreadable"
        why = (why + "; " if why else "") + "flag unreadable (clear of a corrupt flag)"
    flag.unlink(missing_ok=True)
    _record_device_pause(device, "clear", who, why, flag_id, pause_who)
    if sweep:
        sweep_if_interrupted([pause_who])
    return pause_who


# THE ONE QUESTION A PRODUCER ASKS BEFORE IT STARTS WORK (Jas, 2026-10-30: "Fix the gaps in production so when I press
# pause it stops every model from working on tasks"). There are two flags -- the production screen and per-device -- and
# a producer that reads only one of them goes on working through the other. Measured before this existed: of the seven
# producers, exactly one consulted a pause, and only for a non-manager scoper, so pressing pause on both devices still
# let the manager loop start t9580 on swift.
DEFAULT_DEVICES = ("gx10", "thinkpad")


def device_of(role):
    """The device a role's model is served from, or None when that cannot be known. Never guesses a device."""
    try:
        sys.path[:0] = [str(REPO / "scripts"), str(REPO / "projects" / "local-inference")]
        import roles as _roles
        tag = _roles.tag(role or "", "")
        import dispatch_loop as _dl
        return _dl.DEVICE_OF.get(tag) or _dl.DEVICE_OF.get(tag.split(":")[0])
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: cannot tell which device {role!r} runs on ({type(e).__name__}: {e})", file=sys.stderr)
        return None


def must_hold(paused=None, devices=None, known=None, device=None, role=None):
    """(True, why) when Jas has pressed pause and NO model may start work; (False, "") when work may start.

    It answers for BOTH kinds of pause, and a producer cannot ask for only one:
      * the production screen paused                     -> everything holds;
      * every known device paused                        -> everything holds, whoever asked and whatever they named;
      * the device this work would use is paused          -> that work holds, and work elsewhere does not.
    `device` is the device the caller is about to use; `role` resolves one when the caller does not know. Naming
    neither is NOT an escape: a full pause still holds it.
    """
    paused = is_paused() if paused is None else bool(paused)
    if paused:
        return True, "the scheduler is paused (production screen -- Resume on the Hub)"
    devices = paused_devices() if devices is None else dict(devices or {})
    if not devices:
        return False, ""
    known = tuple(known or DEFAULT_DEVICES)
    if all(d in devices for d in known):
        who = ", ".join(sorted({str((devices.get(d) or {}).get("who") or "?") for d in known}))
        return True, (f"ALL devices are paused ({who}) -- {', '.join(sorted(devices))}; "
                      f"Resume on the Hub to let any model work")
    dev = device or (device_of(role) if role else None)
    if dev and dev in devices:
        note = devices.get(dev) or {}
        return True, (f"{dev} is paused ({note.get('who') or '?'}: {note.get('why') or 'no reason given'}) -- "
                      f"Resume on the Hub")
    return False, ""


def paused_devices():
    """{device: note} for every device paused on its own. An unreadable flag still pauses, said."""
    out = {}
    for f in sorted(PAUSE_FLAG.parent.glob("device-paused-*.enabled")):
        dev = f.name[len("device-paused-"):-len(".enabled")]
        try:
            out[dev] = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            out[dev] = {"who": "?", "why": f"flag unreadable ({type(e).__name__})"}
    return out


# A PERSON'S PAUSE KEEPS SWIFT SERVING (Jas, 2026-10-22: "I expect to pause and resume production a lot").
# Stopping production.target stops its PartOf units, Swift among them, so every Hub pause took down the
# GX10 DeepSeek Harness (which runs on Swift) and every Resume paid a multi-minute 93 GB load. Only the
# memory guard needs Swift's memory back; anyone else's pause stops the target's OTHER units.
KEEP_SERVING = ("swift.service",)


def _production_units():
    """The units production.target pulls in, read from systemd (never a hand-kept list)."""
    import subprocess
    r = subprocess.run(["systemctl", "--user", "list-dependencies", PRODUCTION_TARGET, "--plain"],
                       capture_output=True, text=True, timeout=60)
    return [l.strip() for l in (r.stdout or "").splitlines()[1:] if l.strip().endswith(".service")]


def gx10_off(who, why):
    """Pause the GX10's work. The memory guard stops production.target (Swift and the scoping loops);
    a person's pause stops every production unit except Swift, which keeps serving the GX10 DSH and
    Resume starts the rest. The ThinkPad's lanes keep running."""
    pause_device("gx10", who, why)
    if who in INTERRUPTIONS:
        r = _systemctl("stop")
        return r.returncode == 0, (r.stderr or "").strip()
    units = [u for u in _production_units() if u not in KEEP_SERVING]
    if not units:
        return True, ""
    argv = ["systemctl", "--user", "--no-block", "stop", *units]
    refuse_live_systemctl_from_test(argv)
    r = _subprocess.run(argv, capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stderr or "").strip()


def gx10_on():
    """Resume the GX10 (Jas, 2026-10-13: "change the everything button to GX10 so I can manage them
    separately"): clear its device pause AND the global flag (set by a reboot or the old key), start
    production.target. The ThinkPad keeps its own pause, so a reboot leaves it off until its own Resume."""
    whos = [_global_pause_who()]
    resume()
    whos.append(resume_device("gx10", sweep=False))
    sweep_if_interrupted(whos)                # before production starts, never after
    return _start_production()


def swift_on():
    """Serve Swift WITHOUT resuming production (M1, 2026-10-26). swift.service is PartOf=production.target,
    which binds stop only, so the unit can start by itself; until now the only path that started it was a
    full Resume. Every pause stays as it was: the DSH gets its model and no agent picks up work."""
    argv = ["systemctl", "--user", "--no-block", "start", *KEEP_SERVING]
    refuse_live_systemctl_from_test(argv)
    r = _subprocess.run(argv, capture_output=True, text=True, timeout=60)
    return r.returncode == 0, (r.stderr or "").strip()


def _global_pause_who():
    try:
        return json.loads(PAUSE_FLAG.read_text(encoding="utf-8")).get("who") if PAUSE_FLAG.exists() else None
    except (OSError, ValueError):
        return None


def breaker_on():
    whos = [_global_pause_who()]
    resume()
    for dev in paused_devices():            # Resume is the one button: it clears device pauses too
        whos.append(resume_device(dev, sweep=False))
    sweep_if_interrupted(whos)
    return _start_production()


def _start_production():
    """Start each unit production.target pulls in, then the target (t1557, 2026-10-23). The target stays
    'active' with its units dead -- manager-queue and auditor-scoping were stopped under it for 52
    minutes -- and starting an active target starts nothing. Starting a running unit is a no-op. The
    target's own start comes last, through the same _systemctl every pause and resume uses."""
    units = _production_units()
    if units:
        r = _systemctl("start", *units)
        if r.returncode != 0:
            return False, (r.stderr or "").strip()
    r = _systemctl("start")
    return r.returncode == 0, (r.stderr or "").strip()


def boot():
    """At boot the line is ALWAYS off, and the master key is turned off (Jas, 2026-10-09).

    This used to start production.target unless the key said paused. On 2026-10-09 Swift's start
    took the box down (available memory 0.38 GB, swap full, NV_ERR_NO_MEMORY) and Jas rebooted:
    "after a reboot the master key is off and no models boot up." A reboot is usually the
    recovery from a crash, so the one thing it must not do is reload what caused it. Jas presses
    Resume on the Hub when he wants the line back.
    """
    pause("boot", "machine rebooted -- the line stays off until Jas presses Resume")
    # EACH DEVICE OFF TOO (2026-10-13): the Hub resumes the GX10 and the ThinkPad separately now, and
    # Resume GX10 clears the global flag -- without its own pause the ThinkPad would start with it.
    for dev in ("gx10", "thinkpad"):
        pause_device(dev, "boot", "machine rebooted -- stays off until Jas resumes it")
    _systemctl("stop")
    print(f"boot: paused, {PRODUCTION_TARGET} stopped -- press Resume on the Hub to start it")
    return 0


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["boot"]:
        sys.exit(boot())
    if sys.argv[1:] == ["swift"]:
        done, err = swift_on()
        print("swift.service starting; production stays paused" if done else f"swift_on failed: {err}")
        sys.exit(0 if done else 1)
    sys.exit("usage: scheduler_commands.py boot|swift")
