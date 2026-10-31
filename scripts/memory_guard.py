#!/usr/bin/env python3
"""Keep a memory reserve the models cannot take: kill Swift before the box locks up.

    python3 scripts/memory_guard.py run            # the service (memory-guard.service), every second
    python3 scripts/memory_guard.py status         # reserve, available now, last trips

WHY (Jas, 2026-10-09): "we need a way to stop Swift from using all the memory. A couple of GB in
reserve so I can send terminal commands from my phone is needed." That afternoon Swift was
started with MTP on (t1006). memory-watch.jsonl shows it reaching 99 GB within a minute:
available memory went 9.46 GB (20:08:33Z) -> 1.14 (20:09:03) -> 0.38 (20:09:27), swap 6.4 -> 12.9 GB,
and the kernel logged NV_ERR_NO_MEMORY until the box stopped answering. Jas had to reboot it.
On 2026-10-08 the same shape crashed it twice. The kernel's OOM killer did not save it: on the
GB10 the GPU's memory is the same pool, the driver fails allocations rather than the kernel
killing anything, and by the time swap is full nothing gets scheduled to do the killing.

WHAT IT DOES. Every second it reads MemAvailable. Below RESERVE_GB it:
  1. kills the Swift container (`docker kill`, not stop: stop waits for a graceful shutdown that a
     starving box cannot do), then any other vLLM container serve.py started;
  2. pauses the GX10 only (scheduler_commands.gx10_off), so nothing restarts Swift -- the ThinkPad's
     lanes keep running on their own memory (Jas, 2026-10-13: independent guards);
  3. writes one row to workspace/memory-guard.jsonl saying what it saw and what it killed.
It does NOT restart anything. Jas presses Resume when he has looked.

CHEAPEST FIRST (Jas, 2026-10-18: "I agree it should kill better"). Swift was the ONLY victim, and the
most expensive one: ~13 min to reload, the Manager idle the whole time. It was killed at 20:59Z and
21:26Z on 2026-10-18 while test sandboxes and builds -- minutes of work, rerun for free -- kept their
memory. The trigger is unchanged -- RESERVE_GB, 2 GB (Jas: "Keep the trigger at 2GB but kill
processes in the right order") -- but when it trips, the guard stops the cheapest work first, ONE TIER
AT A TIME, and waits SETTLE_S for the memory to come back before taking the next:
  tier 1  test runs (scripts/run_tests.py, scripts/hourly_checks.py and everything under them) --
          rerun next hour; the whole process group is killed;
  tier 2  builds (projects/local-inference/coder.py) -- SIGTERM, which coder.py turns into a
          checkpoint, so the build resumes where it was;
and only if memory is STILL below RESERVE_GB with nothing cheaper left to stop, Swift and the
key-off as before.
Processes are found by reading /proc/<pid>/cmdline, never by `ps | grep` (L-105: the guard's own
command line would match), and the guard never signals itself.

RESERVE. 2 GB (Jas, 2026-10-09: "2 GB is enough"). The fastest fall measured, 9.46 -> 1.14 GB in
30 s, is ~0.3 GB/s, so at one sample every 0.5 s the guard acts with seconds to spare. Override
with MEMORY_GUARD_RESERVE_GB.

IT CANNOT BE SWAPPED OUT. The way a guard like this fails is not CPU: under memory pressure the
kernel evicts its pages, and it cannot run in time to free anything. So it locks itself into RAM
(mlockall; this user's memlock limit is unlimited, `ulimit -l`) and imports everything it will
need BEFORE the loop, so tripping allocates almost nothing new. It is ~13 MB resident. It is deliberately NOT under
production.target: it must run while the line is paused, and it starts at boot.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "workspace" / "memory-guard.jsonl"
RESERVE_GB = float(os.environ.get("MEMORY_GUARD_RESERVE_GB", "2"))
INTERVAL_S = 0.5
SETTLE_S = 1.0          # after stopping a tier, wait this long for its memory to come back
# MEMORY PRESSURE, NOT ONLY FREE MEMORY (Jas, 2026-10-19). systemd-oomd watches user@1000.service's
# memory pressure and kills inside it above 50% (ManagedOOMMemoryPressureLimit). On 2026-10-19 02:15 it
# did -- the scoping service, the hourly run, the Claude desktop app -- with ~14 GB still free (11.6 GB
# of swap in use), and the remote desktop failed from then on. So the guard also reads that same
# pressure and stops the cheap tiers BELOW oomd's line. Pressure alone never kills Swift.
PRESSURE_FILE = Path("/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/memory.pressure")
# A SPIKE IS NOT A SQUEEZE (2026-10-20). At 35% of a 10-second average it stopped the hourly run every
# hour from 01:00Z to 13:00Z with 19-20 GB free -- not one completed -- after Jas had raised oomd's limit
# to 80% (/etc/systemd/system/user@1000.service.d/oomd-limit.conf), and oomd acts only on pressure HELD
# over its limit (~30 s). So: shed on a 10-second spike over PRESSURE_SHED, or a one-minute average
# over PRESSURE_SHED_60 -- both still well under oomd's line, read from its own config.
OOMD_CONF = Path("/etc/systemd/system/user@1000.service.d/oomd-limit.conf")


def oomd_limit(conf=OOMD_CONF):
    """systemd-oomd's ManagedOOMMemoryPressureLimit for the user session, in %; 50 (its default) if unset."""
    try:
        for line in Path(conf).read_text().splitlines():
            if line.strip().startswith("ManagedOOMMemoryPressureLimit="):
                return float(line.split("=", 1)[1].strip().rstrip("%"))
    except (OSError, ValueError) as e:
        print(f"memory-guard: oomd limit unreadable ({type(e).__name__}); assuming 50%", file=sys.stderr)
    return 50.0


# 75, not 60 (2026-10-20 07:08-07:15 local): with 17-19 GB free, 10-second spikes reached 63% while the
# one-minute average sat at 26-27%, and 60 still stopped the hourly run and two builds. A spike that
# short is not what oomd acts on; the one-minute line is the one that catches a real squeeze.
# 80, not 75 (Jas, 2026-10-23: "Change both to 80 and 90"). Since 10-20 the 75 line shed builds 288 times,
# 280 of them at avg10 75-79 with 13.5-22 GB free; at 80 that is 18. oomd acts on 80 HELD for 30 s, so a
# 10-second reading at 80 still comes first on a real squeeze.
PRESSURE_SHED = float(os.environ.get("MEMORY_GUARD_PRESSURE", "80"))      # % some avg10
# 60, not 40 (Jas, 2026-10-20: "Why is the guard tripping at 40%?"). At 40 it stopped builds over and
# over with 17-19 GB free (10:01, 10:07 local). The one oomd kill that day (07:21) was avg10 87.5 /
# avg60 50 -- caught by the 10-second line at 75, not by this one. This line is for a slow, held squeeze.
# 60 (unchanged). On 2026-10-23 this was briefly set to 90 on a misreading: Jas's "80 and 90" meant the
# 10-second line at 80 and systemd-oomd's own limit at 90, not this line.
PRESSURE_SHED_60 = float(os.environ.get("MEMORY_GUARD_PRESSURE_60", "60"))  # % some avg60
# (tier name, argv substrings that mark a process of that tier, signal, triggers that may shed it).
# Cheapest first. THE TRIGGERS (Job B, 2026-10-23, dsh-week/B-hourly-checks-rca.md): the tests tier
# fires on the FREE-RAM RESERVE only. Its PSI-pressure shed killed the hourly sweep 42/42 runs
# (Oct 19-22, every kill at 17-20 GB free): on the GB10 the resident Ollama weights keep
# user@1000.service pressure structurally above the 75/60 line while the free reserve is 8.5x
# the guard's own 2 GB line -- for the cheapest tier, pressure is a false alarm, not the squeeze
# it was built to catch. The builds tier keeps both triggers: builds are the deferrable expensive
# work the pressure path exists for (Jas, 2026-10-19/20: act below oomd's line; a spike is not
# a squeeze). The reserve path (RESERVE_GB) remains the real memory threat for everything,
# tests included -- a real 1.5-GB dip still stops the sweep.
# THE SHED ORDER IS DECLARED DATA NOW (t1597): workspace/guard-tiers.json. The tuple below is the
# FALLBACK and it is also the record of what the guard did before the data existed -- a guard that
# refuses to start because a JSON file is missing is worse than one running last week's order, so the
# order falls back and says so in the ledger. check_workspace fails when the file and the code
# disagree, so the fallback cannot silently become the truth.
DEFAULT_TIERS = (
    ("tests", ("scripts/run_tests.py", "scripts/hourly_checks.py"), "group", ("reserve",)),
    ("builds", ("projects/local-inference/coder.py",), "term", ("reserve", "pressure")),
)
DEFAULT_MODEL = ("model", ("swift",), "trip", ("reserve",))
TIERS_FILE = REPO / "workspace" / "guard-tiers.json"


def load_tiers(path=None):
    """(tiers, model, reserve_gb, where, problem) from the declared file, falling back to the defaults.

    ``where`` is "file" or "defaults"; ``problem`` names why the file was not used, so a caller can say
    it out loud rather than pretend the order is the declared one.
    """
    path = Path(path or TIERS_FILE)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        raw = d["tiers"]
        if not isinstance(raw, list):
            raise ValueError("tiers must be a list")
        out = []
        for t in raw:
            out.append((str(t["name"]), tuple(str(x) for x in t["processes"]),
                        str(t["signal"]), tuple(str(x) for x in t["triggers"])))
        if not out:
            raise ValueError("tiers is empty")
        model = [t for t in out if t[2] == "trip"]
        reserve = float(d.get("reserve_gb", RESERVE_GB))
        return tuple(out), (model[-1] if model else DEFAULT_MODEL), reserve, "file", None
    except (OSError, ValueError, KeyError, TypeError) as e:
        return DEFAULT_TIERS, DEFAULT_MODEL, float(os.environ.get("MEMORY_GUARD_RESERVE_GB", "2")), \
               "defaults", f"{type(e).__name__}: {e}"


def validate_tiers(path=None, repo=None):
    """[problem] for a declared order that cannot be trusted. Empty means the file is usable.

    Called by check_workspace and by ``memory_guard.py --validate``. Three things it will not accept:
    a tier whose process no longer exists (a rename silently stops that tier being shed), the model
    not last (Swift takes 20 minutes to reload, so it is the one thing that must not go first), and a
    reserve that is not the number Jas set without the file saying so.
    """
    path, repo = Path(path or TIERS_FILE), Path(repo or REPO)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        return [f"workspace/guard-tiers.json cannot be read ({type(e).__name__}: {e}) -- the guard is "
                f"running its built-in order, which is not the declared one"]
    except ValueError as e:
        return [f"workspace/guard-tiers.json is not valid JSON ({e})"]
    probs = []
    raw = d.get("tiers")
    if not isinstance(raw, list) or not raw:
        return ["workspace/guard-tiers.json declares no tiers"]
    names = [str(t.get("name")) for t in raw if isinstance(t, dict)]
    if len(names) != len(raw):
        probs.append("every tier must be an object with a name")
    if names and names[-1] != "model":
        probs.append(f"the model must be the LAST tier (found {names[-1]!r} last); Swift is the most "
                     f"expensive thing to reload, so it goes after everything cheaper")
    if "tests" in names and "builds" in names and names.index("tests") > names.index("builds"):
        probs.append("the tests tier must come before builds: it is the cheaper thing to lose")
    # THE FOUR LINES (t1597). The whole point of the split is that they keep their order: shed above
    # the kill line, pause above shed, and resume ABOVE pause so the guard cannot flap on one number.
    lv = d.get("levels") or {}
    try:
        pz, sh, rs_, kl = (float(lv[k]) for k in ("pause_gb", "shed_gb", "resume_gb", "kill_gb"))
    except (KeyError, TypeError, ValueError):
        probs.append("workspace/guard-tiers.json declares no usable levels block (pause_gb, shed_gb, "
                     "resume_gb, kill_gb); without it the guard falls back to its built-in numbers "
                     "and the split is not the declared one")
    else:
        if not kl < sh < pz:
            probs.append(f"the lines must satisfy kill < shed < pause (got kill {kl:g}, shed {sh:g}, "
                         f"pause {pz:g}): shedding above the kill line is what gives cheap work time "
                         f"to come back before the model is touched")
        if rs_ <= pz:
            probs.append(f"resume_gb ({rs_:g}) must be ABOVE pause_gb ({pz:g}), or the guard flaps "
                         f"on and off around a single number")
    for t in raw:
        if not isinstance(t, dict):
            continue
        for proc in t.get("processes") or []:
            if t.get("signal") == "trip":
                # A CONTAINER NAME, not a path: the model tier is docker's "swift" as serve.py runs
                # it, so the repository cannot be asked whether the file exists.
                continue
            if not (repo / str(proc)).exists():
                probs.append(f"tier {t.get('name')!r} names {proc!r}, which does not exist -- a rename "
                             f"would silently stop that tier being shed")
        trig = t.get("triggers") or []
        if not trig:
            probs.append(f"tier {t.get('name')!r} declares no triggers, so it can never fire")
        for x in trig:
            if x not in ("reserve", "pressure"):
                probs.append(f"tier {t.get('name')!r} declares trigger {x!r}; known: reserve, pressure")
    return probs


DEFAULT_LEVELS = {"pause_gb": 10.0, "shed_gb": 8.0, "resume_gb": 12.0, "kill_gb": 2.0,
                  "grace_s": 30.0, "settle_s": 1.0, "interval_s": 0.5}
_LEVELS = {"mtime": None, "v": None}


def levels(path=None):
    """The four lines and the timers, RE-READ WHEN THE FILE CHANGES (t1597).

    This is what makes a settings change on the Devices tab take effect without restarting the
    guard: no systemd, no command line, the next tick obeys. Missing or unreadable falls back to the
    defaults, because a guard that stops guarding over a JSON typo is worse than one using yesterday's
    numbers -- and check_workspace fails while the file is unusable.
    """
    path = Path(path or TIERS_FILE)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return dict(DEFAULT_LEVELS)
    if _LEVELS["mtime"] == mtime and _LEVELS["v"] is not None:
        return _LEVELS["v"]
    out = dict(DEFAULT_LEVELS)
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        for block in ("levels", "timers"):
            for k, v in (d.get(block) or {}).items():
                if k in out:
                    out[k] = float(v)
        if "kill_gb" not in (d.get("levels") or {}) and "reserve_gb" in d:
            out["kill_gb"] = float(d["reserve_gb"])
    except (OSError, ValueError, TypeError):
        return dict(DEFAULT_LEVELS)
    _LEVELS.update(mtime=mtime, v=out)
    return out


def pause_line(why):
    """Stop NEW work, once. A pause already taken by a person is never overwritten."""
    try:
        import scheduler_commands as sc
        if not sc.is_paused():
            sc.pause("memory-guard", why)
            return True
    except Exception as e:                                      # noqa: BLE001 -- must not die
        print(f"memory-guard: could not pause the line: {e!r}", file=sys.stderr, flush=True)
    return False


def resume_line(why=""):
    """Lift OUR pause only. scheduler_commands.resume() deletes the flag whatever took it, so the
    guard reads who owns it first -- resuming a human's pause would silently restart his line."""
    try:
        import scheduler_commands as sc
        try:
            owned = json.loads(sc.PAUSE_FLAG.read_text(encoding="utf-8")).get("who") == "memory-guard"
        except (OSError, ValueError, AttributeError):
            owned = False
        if owned:
            sc.resume()
            print(f"memory-guard: resumed -- {why}", flush=True)
            return True
    except Exception as e:                                      # noqa: BLE001
        print(f"memory-guard: could not resume: {e!r}", file=sys.stderr, flush=True)
    return False


_TIERS, _MODEL, _RESERVE, TIERS_SOURCE, TIERS_PROBLEM = load_tiers()
TIERS = tuple(t for t in _TIERS if t[2] != "trip")
# The model tier's containers (serve.CONTAINER is "swift"), from the tier file. A hard-coded
# CONTAINERS = ("swift",) used to follow this line and silently overrule the file (L1, 2026-10-27;
# scripts/tests/test_memory_guard_slope.py proves an edited file now changes it).
CONTAINERS = tuple(_MODEL[1])


def resolve_reserve():
    """Set RESERVE_GB to the declared kill line. Called by the entry points, not on import: an import
    that reads the tier file is not free (scripts/tests/test_import_is_free.py; L4 found it, 10-27)."""
    global RESERVE_GB
    if os.environ.get("MEMORY_GUARD_RESERVE_GB") or TIERS_SOURCE == "file":
        RESERVE_GB = levels()["kill_gb"]
    return RESERVE_GB


class FallRate:
    """How fast free memory is falling, in GB/s, over the last ``window_s`` seconds (never negative)."""

    def __init__(self, window_s=5.0):
        self.window_s = window_s
        self.samples = []

    def add(self, t, avail):
        self.samples.append((t, avail))
        while self.samples and t - self.samples[0][0] > self.window_s:
            self.samples.pop(0)

    def rate(self):
        if len(self.samples) < 2:
            return 0.0
        (t0, a0), (t1, a1) = self.samples[0], self.samples[-1]
        return max(0.0, (a0 - a1) / (t1 - t0)) if t1 > t0 else 0.0


def split_shed_due(avail, paused_for, rate, L):
    """Below the shed line: shed when the grace is over, OR when the current fall would reach the
    kill line before the grace ends (L1, 2026-10-27). A ~0.43 GB/s collapse (16.7 -> 1.7 GB in 35 s)
    crosses 2 GB about eleven seconds before a 30 s grace started at the pause line runs out, so on
    the grace alone the cheap tiers were never shed and the model died first."""
    if avail >= L["shed_gb"]:
        return False
    left = L["grace_s"] - paused_for
    if left <= 0:
        return True
    return rate > 0 and (avail - L["kill_gb"]) / rate <= left


def available_gb(meminfo="/proc/meminfo"):
    for line in Path(meminfo).read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) / 1048576
    raise RuntimeError("MemAvailable missing from " + meminfo)


def running(names=CONTAINERS):
    r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True,
                       timeout=10)
    up = set(r.stdout.split())
    return [n for n in names if n in up]


def trip(avail, kill=None, breaker=None, ledger=LEDGER, names=CONTAINERS):
    """Below the reserve: kill the model containers, key off, record. Returns the row written."""
    kill = kill or (lambda n: subprocess.run(["docker", "kill", n], capture_output=True, text=True,
                                             timeout=20).returncode == 0)
    if breaker is None:
        sys.path.insert(0, str(REPO / "scripts"))
        import scheduler_commands
        breaker = scheduler_commands.gx10_off
    victims = running(names) if names is CONTAINERS else list(names)
    killed = [n for n in victims if kill(n)]
    ok, err = breaker("memory-guard", f"available {avail:.2f} GB fell below the {RESERVE_GB} GB reserve")
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "available_gb": round(avail, 2),
           "reserve_gb": RESERVE_GB, "killed": killed, "tried": victims, "key_off": ok,
           "key_error": err or None}
    with open(ledger, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    print(f"memory-guard: TRIPPED at {avail:.2f} GB -- killed {killed or 'nothing'}, key off={ok}",
          flush=True)
    return row


def _cmdline(pid):
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return ""               # gone between listdir and read: nothing to stop


def tier_pids(marks, proc="/proc", me=None):
    """Pids whose command line names one of `marks`, read from /proc. Never this process."""
    me = os.getpid() if me is None else me
    out = []
    for name in os.listdir(proc):
        if name.isdigit() and int(name) != me:
            cmd = _cmdline(name)
            if any(m in cmd for m in marks):
                out.append(int(name))
    return out


def stop_tier(tier, find=tier_pids, signal_group=None, signal_pid=None):
    """Stop every process of one tier. Returns the pids signalled."""
    import signal
    name, marks, how = tier[0], tier[1], tier[2]
    signal_group = signal_group or (lambda pid: os.killpg(os.getpgid(pid), signal.SIGKILL))
    signal_pid = signal_pid or (lambda pid: os.kill(pid, signal.SIGTERM))
    hit = []
    for pid in find(marks):
        try:
            (signal_group if how == "group" else signal_pid)(pid)
            hit.append(pid)
        except (ProcessLookupError, PermissionError) as e:
            print(f"memory-guard: {name} pid {pid} not stopped: {e!r}", flush=True)
    return hit


def shed(avail, ledger=LEDGER, tiers=TIERS, stop=stop_tier, measure=None, settle=SETTLE_S, why=None,
          trigger="reserve"):
    """Stop the cheapest tier that has anything running, one per call. Returns the
    row written, or None when no tier had anything to stop (the caller then falls to the hard trip).

    `trigger` is which trip path is shedding: "reserve" (MemAvailable below RESERVE_GB) or
    "pressure" (PSI over the oomd-adjacent line). A tier only sheds under the triggers it declares
    (TIERS 4th field): the tests tier declares "reserve" only, because the PSI path fires structurally
    on the GB10 while the reserve is untouched -- 42/42 sweep kills at 17-20 GB free
    (dsh-week/B-hourly-checks-rca.md)."""
    for tier in tiers:
        if trigger not in (tier[3] if len(tier) > 3 else ("reserve", "pressure")):
            continue
        hit = stop(tier)
        if hit:
            time.sleep(settle)
            after = (measure or available_gb)()
            row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "available_gb": round(avail, 2),
                   "reserve_gb": RESERVE_GB, "stopped": tier[0], "pids": hit, "available_after_gb": round(after, 2),
                   "why": why or f"available below {RESERVE_GB} GB"}
            with open(ledger, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
            print(f"memory-guard: {why or f'below {RESERVE_GB} GB'} at {avail:.2f} GB free -- stopped "
                  f"{tier[0]} {hit}, now {after:.2f} GB", flush=True)
            return row
    return None


def pressure(path=PRESSURE_FILE, window="avg10"):
    """`some <window>` of the user session's memory pressure, in %, or None if unreadable."""
    try:
        for line in Path(path).read_text().splitlines():
            if line.startswith("some "):
                return float(line.split(f"{window}=")[1].split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return None


def pin():
    """mlockall(MCL_CURRENT | MCL_FUTURE): this process's pages stay in RAM. Returns True if locked."""
    import ctypes
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    return libc.mlockall(1 | 2) == 0


def run():
    resolve_reserve()
    sys.path.insert(0, str(REPO / "scripts"))
    import scheduler_commands                     # noqa: F401 -- loaded now, not mid-crisis
    locked = pin()
    print(f"memory-guard: locked in RAM={locked}", flush=True)
    print(f"memory-guard: reserve {RESERVE_GB} GB, checking every {INTERVAL_S:g} s", flush=True)
    print(f"memory-guard: below {RESERVE_GB} GB it stops, in order: "
          f"{', '.join(t[0] for t in TIERS)}, then Swift", flush=True)
    armed = True
    pressure_armed = True
    paused, paused_at = False, 0.0
    print(f"memory-guard: pause {levels()['pause_gb']:g} GB -> shed {levels()['shed_gb']:g} GB after "
          f"{levels()['grace_s']:g} s -> resume {levels()['resume_gb']:g} GB -> kill {levels()['kill_gb']:g} GB",
          flush=True)
    fall = FallRate()
    while True:
        L = levels()                      # re-read: an edited level is obeyed on the next tick
        avail = available_gb()
        fall.add(time.monotonic(), avail)
        # THE SPLIT (t1597, Jas 2026-10-25). ONE number used to do two jobs -- shed cheap work and
        # decide to kill the model -- at 2 GB, which is far too late to shed: the guard's own notes
        # record a fall from 9.46 GB to 1.14 GB inside a minute. So: pause new work high up, WAIT so
        # the pause can show up in free memory, then shed ONE tier, and resume only on the higher
        # line so it cannot flap.
        if avail < L["pause_gb"]:
            if not paused:
                paused, paused_at = True, time.monotonic()
                pause_line(f"{avail:.2f} GB free, below the {L['pause_gb']:g} GB pause line")
                continue
            paused_for = time.monotonic() - paused_at
            if split_shed_due(avail, paused_for, fall.rate(), L):
                try:
                    if shed(avail, trigger="reserve",
                            why=f"{avail:.2f} GB free, below the {L['shed_gb']:g} GB shed line "
                                f"after {paused_for:.0f} s paused, falling {fall.rate():.2f} GB/s") is not None:
                        continue             # one tier, then re-measure; never two at once
                except Exception as e:                          # noqa: BLE001 -- must not die
                    print(f"memory-guard: split shed failed: {e!r}", file=sys.stderr, flush=True)
        elif paused and avail >= L["resume_gb"]:
            resume_line(f"{avail:.2f} GB free, above the {L['resume_gb']:g} GB resume line")
            paused = False

        pr, pr60 = pressure(), pressure(window="avg60")
        squeezed = (pr is not None and pr >= PRESSURE_SHED) or (pr60 is not None and pr60 >= PRESSURE_SHED_60)
        if squeezed and pressure_armed:
            try:
                f0 = lambda v: "?" if v is None else f"{v:.0f}"
                row = shed(avail, stop=stop_tier, trigger="pressure",
                           why=f"pressure {f0(pr)}% (10 s) / {f0(pr60)}% (60 s), "
                           f"shed at {PRESSURE_SHED:.0f}/{PRESSURE_SHED_60:.0f}; oomd acts at {oomd_limit():.0f}%")
                if row is not None:
                    continue                         # re-measure; the next tier only if still under pressure
            except Exception as e:                    # noqa: BLE001 -- the guard must not die
                print(f"memory-guard: pressure shed failed: {e!r}", file=sys.stderr, flush=True)
            pressure_armed = False                   # nothing cheaper left: wait for it to ease
        elif pr is not None and pr < PRESSURE_SHED - 15 and (pr60 is None or pr60 < PRESSURE_SHED_60 - 10):
            pressure_armed = True
        if avail < L["kill_gb"] and armed:
            try:
                if shed(avail) is not None:          # a cheaper tier was stopped and waited for:
                    continue                         # re-measure now; the next tier only if still low
            except Exception as e:                    # noqa: BLE001 -- the guard must not die
                print(f"memory-guard: shed failed: {e!r}", file=sys.stderr, flush=True)
            try:
                trip(available_gb())
            except Exception as e:                    # noqa: BLE001 -- the guard must not die
                print(f"memory-guard: trip failed: {e!r}", file=sys.stderr, flush=True)
            armed = False                             # once per dip, not once per second
        elif avail >= L["kill_gb"] + 2:
            armed = True
        time.sleep(INTERVAL_S)


def show_tiers():
    """What the guard would shed, in order, and where that order came from (t1597)."""
    resolve_reserve()
    print(f"  order from: {TIERS_SOURCE}" + (f"  ({TIERS_PROBLEM})" if TIERS_PROBLEM else ""))
    print(f"  reserve: {RESERVE_GB:g} GB")
    for name, procs, sig, trig in _TIERS:
        print(f"  {name:8s} {sig:6s} triggers={','.join(trig):18s} {', '.join(procs)}")
    print("  the model is LAST on purpose: it takes ~20 minutes to reload")
    return 0


def status():
    resolve_reserve()
    print(f"  reserve {RESERVE_GB} GB; available now {available_gb():.2f} GB")
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-5:]:
            print("  " + line)
    else:
        print("  never tripped")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1:2] or ["status"]
    if cmd == ["run"]:
        run()
    elif cmd == ["status"]:
        sys.exit(status())
    elif cmd == ["tiers"]:
        sys.exit(show_tiers())
    elif cmd in (["--validate"], ["validate"]):
        probs = validate_tiers()
        for x in probs:
            print("  FAIL " + x)
        print(f"  {len(probs)} problem(s); order from: {TIERS_SOURCE}")
        sys.exit(1 if probs else 0)
    else:
        sys.exit("usage: memory_guard.py run|status|tiers|--validate")
