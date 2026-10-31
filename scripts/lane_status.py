#!/usr/bin/env python3
"""What every running local-model dispatch is doing, right now.

Jas, 2026-09-14: "I just want to make sure that Qwen 16-bit is still running. Is
it possible to put in a process to actually see what's going on inside the model
or something, so we know definitively if something is working?"

THE PROBLEM THIS SOLVES. A dispatch to a local model is silent for an hour at a
time. `ps` shows the process at 0.0% CPU -- correct, and useless, because the
process is blocked on an HTTP call while the GPU does the work. Nothing is
written to the repository until the run ENDS. So the only honest question,
"is it moving or is it wedged", had no answer short of guessing from elapsed time.

WHAT COUNTS AS EVIDENCE HERE, in order of how much it proves:

  1. THE LOG IS STILL GROWING. A per-run live log whose mtime is seconds old is a
     lane that just printed something. This is the only signal that proves the
     HARNESS is alive, as opposed to the GPU being busy on someone else's work.
  2. THE GPU IS BUSY. nvidia-smi utilisation. Proves SOMETHING is decoding; with
     two lanes up it does not say which, which is exactly why (1) is ranked first.
  3. THE MODEL IS RESIDENT. ollama /api/ps. Proves the weights are loaded and not
     about to be paged, and gives the expiry -- a model whose keep-alive lapses
     mid-run pays the load cost again.

A lane can be alive on (2) and (3) and still be wedged, which is why this reports
all three separately and never collapses them into one word. The old version of
this question got answered with "the GPU is at 95%, so it is fine" -- true, busy,
and silent about which of the two lanes was actually producing tokens.

RATE. Decode speed is read from workspace/run-telemetry.jsonl, per model, from
runs that actually finished -- not estimated from this run, which cannot be
measured until it ends. Expected time per attempt is num_predict / that rate, and
it is reported as a RANGE across the observed runs rather than a single number,
because contention moves it by more than 2x and a single number would read as a
promise.
"""
import calendar
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TELEMETRY = REPO / "workspace" / "run-telemetry.jsonl"
# Where coder.py writes it: TMPDIR-aware since t415, so a literal /tmp here missed lanes
# started with TMPDIR set.
LIVE_DIR = Path(tempfile.gettempdir()) / "coder-live"
# The pre-2026-09-14 shared log. Read if present so a lane started by the OLD code
# is not reported as having no log at all -- which would read as "wedged".
LEGACY_LIVE = Path(tempfile.gettempdir()) / "coder-live.log"


def _run(*a):
    try:
        r = subprocess.run(a, capture_output=True, text=True, timeout=15)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


# EVERY JOB THAT OCCUPIES A MODEL IS A LANE, not just production (2026-09-16).
# Jas: "I don't see any lanes open. Whatever they are doing I want to see as a lane."
# This recognised coder.py and nothing else, so a preflight review, a script review or a
# supervision pass could hold a model for twenty minutes and the Status tab showed an idle
# box. Every one of these is started by dispatch_loop.py against a model lease, so every one
# of them is a lane by the only definition that matters -- the model is busy.
# PINNED BY scripts/tests/test_lane_contract.py (L-152): widening this is correct for the
# Status tab and WRONG for the scheduler, which filters to production before using it.
LANE_SCRIPTS = {
    "coder.py": "production",
    "preflight_brief.py": "preflight",
    "supervise.py": "supervise",
    "truncation_review.py": "script-review",
    "console.py": "console",
    # A MANAGER RUN IS A LANE TOO (2026-09-23). ask_q16.py drives the manager model for minutes
    # to hours; unlisted, the Q4 queue was invisible here and to the scheduler's memory count.
    "ask_q16.py": "manager",
}
# THE MODEL A SCRIPT USES WHEN ITS ARGV DOES NOT SAY. preflight showed "(default)" because it
# takes the manager's model implicitly; name the role instead of guessing.
SCRIPT_ROLE = {"ask_q16.py": "manager", "preflight_brief.py": "manager",
               "supervise.py": "manager", "truncation_review.py": "auditor"}
RECENT_S = 1800          # "recent" decode rate: calls that FINISHED in the last half hour


def script_of(argv):
    """The .py this interpreter is running, or None. Skips the interpreter's OWN flags.

    `python3 -u coder.py` put `-u` in argv[1], and the old test looked only at argv[1] -- so
    any run with an interpreter flag was invisible, which is a silent wrong answer rather
    than an error. Found 2026-09-16 with a live coder.py that the Status tab could not see.
    `-c` and `-m` are refused outright: their argument is code or a module name, and a `.py`
    appearing inside a -c string is not a script this process is running.
    """
    if len(argv) < 2 or not Path(argv[0]).name.startswith("python"):
        return None
    i = 1
    while i < len(argv) and argv[i].startswith("-"):
        if argv[i] in ("-c", "-m"):
            return None
        i += 1
    if i >= len(argv) or not argv[i].endswith(".py"):
        return None
    return Path(argv[i]).name


def lanes():
    """Every live model-occupying job, read from /proc rather than by matching a name.

    A name search over command lines matches the shell running it (L-105) and reports a dead
    process as alive. /proc/<pid>/cmdline is the process's own memory: if the
    directory is gone the process is gone, and a zombie has an empty cmdline.
    """
    out = []
    for d in sorted(Path("/proc").glob("[0-9]*")):
        try:
            argv = (d / "cmdline").read_bytes().decode().split("\0")
            argv = [x for x in argv if x]
            # An interpreter running one of these AS ITS SCRIPT. `timeout`, `nohup` and
            # `bash -c` wrappers also carry the path in their argv and are not lanes --
            # counting them reported one dispatch as two, and as a duplicate of itself.
            name = script_of(argv)
            if name not in LANE_SCRIPTS:
                continue
            # THE CONSOLE SERVER IS NOT A JOB (2026-10-08: Jas saw it on Lanes as "main (default)").
            # agent-console.service runs console.py with no arguments, permanently; a console JOB is
            # `console.py --answer <id> <model>`, started by the scheduler.
            if name == "console.py" and "--answer" not in argv:
                continue
            # A TEST SUITE'S SANDBOX COPY IS NOT A JOB (2026-10-14: the Frontline tile read WORKING on
            # "mark the inbox README heading", model faketest -- a coder suite's fake run during a sweep,
            # with Frontline at 0 lanes). Real lanes run the repository's own scripts.
            path = next((a for a in argv if a.endswith(name)), "")
            if path.startswith("/") and not path.startswith(str(REPO)):
                continue
            stat = (d / "stat").read_text().split(") ", 1)[1].split()[0]
            if stat == "Z":                     # finished, not yet reaped
                continue
            started = d.stat().st_mtime
            out.append({"pid": int(d.name), "argv": argv, "stat": stat,
                        "kind": LANE_SCRIPTS[name], "script": name,
                        "elapsed": time.time() - started})
        except (OSError, IndexError, UnicodeDecodeError) as e:
            if d.exists():           # gone = exited mid-scan, normal; still there = said
                print(f"  NOTE: {d} unreadable ({e}) -- not counted as a lane", file=sys.stderr)
            continue
    return out


def arg(argv, flag, default=""):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) \
        else default


# A RUN THAT GENERATED ALMOST NOTHING CANNOT MEASURE DECODE SPEED. tok_s_decode on
# a 47-token reply is dominated by prompt processing and scheduling, and the
# telemetry is full of them: the bf16 rows include 0.03, 0.09 and 1.3 tok/s, all
# from replies under 120 tokens, next to 8-10 tok/s from real ones. Averaging the
# two together produced a "0.03-10.29 tok/s" range and an estimate of "26 minutes
# to 148 hours", which is not an answer to any question. Sample only runs that
# actually decoded.
MIN_DECODE_SAMPLE = 1000


def _median(v):
    v = sorted(v)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def rates():
    """model -> (median, min, max, n) decode tok/s over runs that really decoded."""
    by = {}
    if not TELEMETRY.exists():
        return by
    for line in TELEMETRY.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            print(f"  NOTE: unreadable line in {TELEMETRY} -- not counted", file=sys.stderr)
            continue
        t, n = r.get("tok_s_decode"), r.get("eval_count") or 0
        if r.get("model") and isinstance(t, (int, float)) and t > 0 \
                and n >= MIN_DECODE_SAMPLE:
            by.setdefault(r["model"], []).append(t)
    return {m: (_median(v), min(v), max(v), len(v)) for m, v in by.items()}


def rates_recent(window_s=RECENT_S, now=None):
    """model -> (median tok/s, calls, seconds since the newest) over calls that FINISHED in the
    last `window_s`. Ollama exposes no rate for a request still running, so this is the closest
    honest "live" number: it moves with contention and reloads, where the all-time median
    does not. Any size of call counts -- a short call decodes at the same speed."""
    now = now or time.time()
    by, unreadable = {}, 0
    if not TELEMETRY.exists():
        return {}
    for line in TELEMETRY.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            at = calendar.timegm(time.strptime(r["at"], "%Y-%m-%dT%H:%M:%SZ"))
        except (ValueError, KeyError, TypeError):
            unreadable += 1                 # counted, said below; a torn row is not a rate
            continue
        t = r.get("tok_s_decode")
        if r.get("model") and isinstance(t, (int, float)) and t > 0 and now - at <= window_s:
            by.setdefault(r["model"], []).append((t, now - at))
    if unreadable:
        print(f"  NOTE: {unreadable} telemetry row(s) unreadable; not in the recent rate",
              file=sys.stderr)
    return {m: (_median([x[0] for x in v]), len(v), min(x[1] for x in v)) for m, v in by.items()}


DISPATCH = REPO / "workspace" / "dispatch-loop.jsonl"


def _ts(at):
    try:
        return calendar.timegm(time.strptime(at, "%Y-%m-%dT%H:%M:%SZ"))
    except (TypeError, ValueError):
        return None


def build_progress(ledger=None, now=None, lanes=2):
    """Today's production builds as done/total, with an ETA from the MEASURED median build time.

    Jas, 2026-09-24: "Is it possible for lane status to show 1/10, 2/10 and so on so I can get
    an idea of ETA?" The scheduler's latest tick says what is running and what is waiting for a
    free model; finished builds since 00:00 UTC are 'done'. Held and parked chunks are NOT in the
    total -- they wait on a person, and an ETA that counted them would be a promise nobody keeps.
    ETA = each running build's remaining median time + waiting builds / lanes x median."""
    ledger = ledger or DISPATCH
    now = now or time.time()
    day = time.strftime("%Y-%m-%dT00:00:00Z", time.gmtime(now))
    started, durs, done, accepted, tick = {}, [], 0, 0, None
    unreadable = 0
    if not Path(ledger).exists():
        return None
    for line in Path(ledger).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            unreadable += 1
            continue
        if r.get("event") == "tick":
            tick = r
        if r.get("kind") != "production":
            continue
        if r.get("event") == "started":
            started[r.get("pid")] = r.get("at")
        elif r.get("event") == "finished":
            a, b = _ts(started.pop(r.get("pid"), None)), _ts(r.get("at"))
            if a and b and b >= a:
                durs.append(b - a)
            if str(r.get("at", "")) >= day:
                done += 1
                accepted += r.get("exit") == 0
    import jsonl_cache
    tick = jsonl_cache.latest_tick(ledger) or tick          # t675: the lists live in the latest-tick file
    if tick is None:
        return None
    why = [s[1] if isinstance(s, (list, tuple)) else s.get("why", "")
           for s in tick.get("skipped", [])]
    running = sum(1 for w in why if w.startswith("already running"))
    # BUILDS ONLY (2026-10-01). `queued` holds every kind the tick deferred -- on 2026-10-01 it
    # was 138 script reviews and 40 RCAs and no build at all -- and counting them here printed
    # "177 builds waiting, ETA ~22h" at the build median. The other kinds are said separately.
    queued = tick.get("queued") or []
    other = {}
    for q in queued:
        k = q.get("kind", "?") if isinstance(q, dict) else "?"
        if k != "production":
            other[k] = other.get(k, 0) + 1
    waiting = (sum(1 for w in why if w.startswith("waiting"))
               + sum(1 for q in queued if isinstance(q, dict) and q.get("kind") == "production"))
    held = sum(1 for w in why if w.startswith(("HELD", "PARKED")))
    review = sum(1 for w in why if w.startswith("DELIVERED"))
    med = _median(durs) if durs else None
    eta = None
    if med:
        # `started` now holds only builds with no finished row: the ones running now.
        run_left = [max(0.0, med - (now - (_ts(at) or now))) for at in started.values()]
        eta = (max(run_left) if run_left else 0) + (waiting / max(1, lanes)) * med
    return {"done": done, "accepted": accepted, "running": running, "waiting": waiting,
            "total": done + running + waiting, "held": held, "review": review,
            "median_s": med, "eta_s": eta, "builds_measured": len(durs), "unreadable": unreadable,
            "other_queued": other}


MANAGER_QUEUE = REPO / "workspace" / "run-logs" / "manager-queue.json"


def manager_queue(pointer=None, now=None):
    """The manager's queue: every job with its state, done/total, and an ETA.

    Jas, 2026-09-24: lane_status showed only the ONE manager job running, not the seven on its
    plate. The queue runner (a prompt directory, an answer directory, a progress log) says where
    it is in workspace/run-logs/manager-queue.json: {"in": dir, "out": dir, "progress": file}.
    A job is DONE when its answer file is non-empty, RUNNING when the log has START without END,
    otherwise PENDING. ETA = pending x the median of this queue's finished job times, plus what
    is left of the running one. None when there is no pointer."""
    pointer = Path(pointer or MANAGER_QUEUE)
    now = now or time.time()
    try:
        cfg = json.loads(pointer.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ind, outd, prog = Path(cfg["in"]), Path(cfg["out"]), Path(cfg["progress"])
    names = sorted(p.stem for p in ind.glob("*.txt")) if ind.is_dir() else []
    starts, durs = {}, []
    if prog.is_file():
        for line in prog.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"(\d\d):(\d\d):(\d\d)Z (START|END) (\S+)", line)
            if not m:
                continue
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if m.group(4) == "START":
                starts[m.group(5)] = t
            elif m.group(5) in starts:
                d = (t - starts.pop(m.group(5))) % 86400
                if m.group(5) in names:
                    durs.append(d)
    day = now % 86400
    jobs = []
    for n in names:
        ans = outd / f"{n}.md"
        if ans.is_file() and ans.stat().st_size > 0:
            jobs.append({"job": n, "state": "done", "bytes": ans.stat().st_size})
        elif n in starts:
            jobs.append({"job": n, "state": "running", "elapsed_s": (day - starts[n]) % 86400})
        else:
            jobs.append({"job": n, "state": "pending"})
    med = _median(durs) if durs else cfg.get("typical_s")
    run = [j for j in jobs if j["state"] == "running"]
    pend = sum(1 for j in jobs if j["state"] == "pending")
    eta = None
    if med:
        eta = pend * med + sum(max(0, med - j["elapsed_s"]) for j in run)
    return {"jobs": jobs, "done": sum(1 for j in jobs if j["state"] == "done"),
            "total": len(jobs), "eta_s": eta, "median_s": med, "measured": len(durs)}


def role_queues(tick=None, d=None, scoping=None):
    """{role: [(task, [waiting keys])]} -- what is WAITING for each model role, nothing running.

    Jas, 2026-10-08: "it should list the queue for all roles. It doesn't need to show what is being
    worked on as that is duplicated in the running jobs section." The scheduler's latest tick lists
    every queued item by kind; each kind goes to the role whose list has it (task_routing.owner),
    in that role's list order. Scoping's queue is manager_next's whole order, minus claimed tasks."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import task_routing as T
    d = d or T.load()
    if tick is None:
        try:
            tick = json.loads((REPO / "workspace" / "run-logs" / "dispatch-tick-latest.json").read_text())
        except (OSError, ValueError) as e:
            print(f"  NOTE: latest tick unreadable ({e})", file=sys.stderr)
            tick = {}
    waiting = {}
    for q in tick.get("queued") or []:
        waiting.setdefault(T.KIND_TASK.get(q.get("kind"), q.get("kind")), []).append(str(q.get("key")))
    if scoping is None:
        try:
            import manager_next
            scoping = manager_next.upcoming(10000)[0]
        except Exception as e:                                      # noqa: BLE001 -- said, not swallowed
            print(f"  NOTE: scoping queue unreadable ({type(e).__name__}: {e})", file=sys.stderr)
            scoping = []
    waiting["scoping"] = list(scoping)
    out = {}
    for role in T.model_roles(d):
        rows = [(t["task"], waiting.get(t["task"], [])) for t in T.tasks_of(d, role)]
        if rows:
            out[role] = rows
    return out


def manager_view(lanes_now, pointer=None, now=None, next_n=5):
    """The manager's plate as an open-ended queue: what is running, what finished today, what is
    next from the top of QUEUE.md, and an ETA for those next jobs.

    Rewritten 2026-09-24: the first version listed a FIXED job directory, which went blank the
    moment the manager switched to taking the top of QUEUE.md -- the new directory was empty and
    the running job belonged to the old runner. Running jobs now come from the live lanes (any
    runner), finished ones from every progress log named, next ones from manager_next."""
    now = now or time.time()
    running = [{"job": (L.get("brief") or "").replace("claude-driven manager: ", "")
                        .replace("manager: ", "").replace(" (top of QUEUE.md)", ""),
                "elapsed_s": L.get("elapsed_s") or 0}
               for L in lanes_now if L.get("kind") == "scoping"]
    logs = []
    try:
        cfg = json.loads(Path(pointer or MANAGER_QUEUE).read_text(encoding="utf-8"))
        logs = [Path(x) for x in ([cfg["progress"]] + list(cfg.get("also", [])))]
    except (OSError, ValueError, KeyError) as e:
        print(f"  NOTE: manager-queue pointer unreadable ({type(e).__name__})", file=sys.stderr)
    today, done, durs = time.strftime("%Y-%m-%d", time.gmtime(now)), [], []
    for lg in logs:
        if not lg.is_file() or time.strftime("%Y-%m-%d", time.gmtime(lg.stat().st_mtime)) != today:
            continue
        starts = {}
        # ONLY TODAY'S LINES (2026-10-08: "851 done today" was every END since the log began). The
        # log stamps times without dates; it writes at least every few minutes (IDLE/YIELD lines),
        # so a time earlier than the one before it is a midnight. Keep what follows the last one.
        lines = lg.read_text(encoding="utf-8", errors="replace").splitlines()
        cut, prev = 0, -1
        for i, line in enumerate(lines):
            m = re.match(r"(\d\d):(\d\d):(\d\d)Z ", line)
            if m:
                t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                if t < prev - 3600:
                    cut = i
                prev = t
        for line in lines[cut:]:
            m = re.match(r"(\d\d):(\d\d):(\d\d)Z (START|END) (\S+)(?:.*bytes=(\d+))?", line)
            if not m:
                continue
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if m.group(4) == "START":
                starts[m.group(5)] = t
            elif m.group(5) in starts:
                durs.append((t - starts.pop(m.group(5))) % 86400)
                done.append({"job": m.group(5), "bytes": int(m.group(6) or 0)})
    nxt = []
    try:
        import manager_next
        nxt, _ = manager_next.upcoming(next_n)
    except Exception as e:                                          # noqa: BLE001
        print(f"  NOTE: next-up unavailable ({type(e).__name__}: {e})", file=sys.stderr)
    med = _median(durs) if durs else 1800
    eta = sum(max(0, med - r["elapsed_s"]) for r in running) + len(nxt) * med
    return {"running": running, "done": done, "next": nxt, "median_s": med,
            "measured": len(durs), "eta_next_s": eta}


def sweep_progress(journal=None, now=None):
    """'312/340 suites, 3 failing so far, running 41m' for a sweep in progress (Jas, 2026-10-01:
    "I want to see how many runs out of the 340 are remaining"), or '' when none is running.
    Read from run_tests' own journal: its 'start' row, the suite rows after it, no 'sweep' end row."""
    import json as _j
    p = Path(journal or (REPO / "workspace" / "sweep-journal.jsonl"))
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]
    except OSError:
        return ""
    rows, torn = [], 0
    for l in lines:
        try:
            rows.append(_j.loads(l))
        except ValueError:
            torn += 1                               # a torn line is not a suite; said below
    if torn:
        print(f"  NOTE: {torn} unreadable sweep-journal line(s) not counted", file=sys.stderr)
    start = max((i for i, r in enumerate(rows) if r.get("kind") == "start"), default=None)
    if start is None or any(r.get("kind") == "sweep" for r in rows[start + 1:]):
        return ""
    st = rows[start]
    if (now or time.time()) - float(st.get("epoch") or 0) > 3 * 3600:
        return ""                                   # a sweep that never wrote its end row: not live
    done = [r for r in rows[start + 1:] if r.get("kind") == "suite"]
    # Suites the cache skipped are DONE too (2026-10-19): run_tests writes one "cached" row with their count.
    crow = [r for r in rows[start + 1:] if r.get("kind") == "cached"]
    cached = sum(int(r.get("n") or 0) for r in crow)
    cached_red = sum(int(r.get("red") or 0) for r in crow)
    # EXPECTED vs NEW (Jas, 2026-10-19: "I can't tell what is ok and expected and what is a problem").
    # Expected = on the ceiling (declared, with an owning task); NEW = not on it -- the only ones that block.
    try:
        allowed = set(_j.loads((REPO / "workspace" / "test-failures-ceiling.json").read_text())
                      .get("allowed_to_fail") or [])
    except (OSError, ValueError):
        allowed = None
    failed = [r.get("suite") for r in done if r.get("rc")]
    mins = int(((now or time.time()) - float(st.get("epoch") or 0)) // 60)
    if allowed is None:
        fails = f"{len(failed)} failing so far"
    else:
        new = [f for f in failed if f not in allowed]
        fails = (f"{len(new)} NEW failure(s)" + (f" ({', '.join(new[:3])}{' ...' if len(new) > 3 else ''})" if new else "")
                 + f", {len(failed) - len(new)} expected failure(s) re-run")
    return (f"{st.get('by') + ': ' if st.get('by') else ''}{len(done) + cached}/{st.get('total')} suites"
            + (f" ({cached} unchanged, skipped -- {cached_red} of them expected failures; {len(done)} run)"
               if cached else "")
            + f"; {fails}; running {mins}m")


def print_manager(d):
    mv = manager_view(d["lanes"])
    basis = (f"median job {hms(mv['median_s'])} over {mv['measured']} today" if mv["measured"]
             else "no job finished today; assuming 30m")
    empty = sum(1 for x in mv["done"] if not x["bytes"])
    print(f"\n  MANAGER QUEUE  {len(mv['done'])} done today"
          + (f" ({empty} empty)" if empty else "")
          + f" · {len(mv['running'])} running · next {len(mv['next'])} ETA ~{hms(mv['eta_next_s'])}"
          f" ({basis})")
    for r in mv["running"]:
        print(f"    ▶ {r['job']:<36} running {hms(r['elapsed_s'])}")
    for x in mv["done"][-4:]:
        print(f"    ✓ {x['job']:<36} {x['bytes']:,} bytes")
    for t in mv["next"]:
        print(f"    · {t:<36} next from the top of QUEUE.md")


def live_log(pid):
    """(path, mtime, last meaningful line) for this lane, or (None, ...)."""
    p = LIVE_DIR / f"{pid}.log"
    if not p.exists():
        if not LEGACY_LIVE.exists():
            return None, 0, ""
        p = LEGACY_LIVE                         # shared, pre-fix: cannot attribute
    try:
        lines = [x.rstrip() for x in p.read_text(encoding="utf-8",
                                                 errors="replace").splitlines()]
        last = next((x for x in reversed(lines) if x.strip()), "")
        return p, p.stat().st_mtime, last
    except OSError:
        return None, 0, ""


def gpu():
    out = _run("nvidia-smi", "--query-gpu=name,utilization.gpu,power.draw,temperature.gpu",
               "--format=csv,noheader")
    return out.strip().splitlines()


def thermal():
    """(hottest system sensor C, its critical C, count) from /sys/class/thermal; None if unreadable.

    Jas, 2026-09-26: the box shut itself off at 10:45 -- hot to the touch, no shutdown in the
    log -- and he wants the temperature on this screen while he watches whether a cooling pad
    is enough. Every zone here trips at 104.8 C.
    """
    hot = crit = None
    n = 0
    for z in Path("/sys/class/thermal").glob("thermal_zone*"):
        try:
            t = int((z / "temp").read_text()) / 1000
        except (OSError, ValueError) as e:
            print(f"  NOTE: {z.name} unreadable: {e}", file=sys.stderr)
            continue
        n += 1
        c = None
        for tp in z.glob("trip_point_*_type"):
            try:
                if tp.read_text().strip() == "critical":
                    c = int(Path(str(tp)[:-5] + "_temp").read_text()) / 1000
            except (OSError, ValueError) as e:
                print(f"  NOTE: {tp.name} of {z.name} unreadable: {e}", file=sys.stderr)
                continue
        if hot is None or t > hot:
            hot, crit = t, c
    return (hot, crit, n) if n else None


# EVERY OLLAMA SERVER ON THE BOX, FOUND, NOT LISTED (Jas, 2026-09-28: "show all ports, including
# potential future ones that are created and destroyed"). A server is any local TCP port in LISTEN
# that answers /api/version like Ollama; a model runner's own port (llama-server --port) is not one.
# The names are only labels for the two we know; any other server shows as "port N".

# SHORT-LIVED MEMO (2026-10-08, Jas: "things seem to load slower now"). A Lanes load probed every
# listening port for Ollama (~2 s), walked /proc and ran nvidia-smi -- some of it twice -- ~4 s warm.
# Machine state a few seconds old is fine for a page; tests pass run= and so are never memoized.
_MEMO = {}


def _memo(ttl):
    def wrap(fn):
        def inner(*a, **k):
            if k.get("run") is not None:
                return fn(*a, **k)
            key = (fn.__name__, a, tuple(sorted(k.items())))
            hit = _MEMO.get(key)
            now = time.time()
            if hit and now - hit[0] < ttl:
                return hit[1]
            val = fn(*a, **k)
            _MEMO[key] = (now, val)
            return val
        inner.__name__, inner.__doc__, inner.uncached = fn.__name__, fn.__doc__, fn
        return inner
    return wrap

SERVER_NAMES = {11434: "main", 11435: "station 2"}


def _listening_ports():
    ports = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(f).read_text().splitlines()[1:]
        except OSError as e:          # no IPv6 table on this kernel is normal; say so, move on
            print(f"  NOTE: {f} unreadable ({e.strerror}); its ports are not scanned", file=sys.stderr)
            lines = []
        for line in lines:
            parts = line.split()
            if len(parts) > 3 and parts[3] == "0A":            # TCP_LISTEN
                ports.add(int(parts[1].rsplit(":", 1)[1], 16))
    return ports


def _runner_ports():
    out = set()
    for pid in (d for d in os.listdir("/proc") if d.isdigit()):
        cmd = _proc(pid, "cmdline").split(b"\0")
        if any(b"llama-server" in c or b"runner" == c for c in cmd):
            for i, c in enumerate(cmd[:-1]):
                if c == b"--port" and cmd[i + 1].isdigit():
                    out.add(int(cmd[i + 1]))
    return out


@_memo(30)
def servers():
    """[(label, port)] of every Ollama server answering on this box right now, main first."""
    import http.client
    import urllib.request
    found = []
    for port in sorted(_listening_ports() - _runner_ports()):
        try:
            # /api/version alone is not enough: another local service on :11000 answers it too
            # (2026-09-28). Ollama's /api/ps returns a JSON object with "models".
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ps", timeout=1) as r:
                ok = "models" in json.loads(r.read(200000) or b"{}")
        except (OSError, ValueError, http.client.HTTPException):
            ok = False                     # not an Ollama server: most listening ports are not (ssh, ...)
        if ok:
            found.append((SERVER_NAMES.get(port, f"port {port}"), port))
    return found


def _server_port(pid):
    """The Ollama port a job's model calls go to: its HIVEMIND_OLLAMA, else the main server."""
    env = _proc(pid, "environ").decode(errors="replace")
    m = re.search(r"(?:^|\0)HIVEMIND_OLLAMA=[^\0]*:(\d+)", env)
    return int(m.group(1)) if m else 11434


def memory():
    """Total / used / free GiB for the whole box, from /proc/meminfo. On the GB10 the GPU has no
    memory of its own -- it shares this pool, which is why nvidia-smi reports N/A for it -- so this
    IS the memory the models load into. Free = MemAvailable (what can be had without swapping).
    Swap is deliberately not shown (Jas, 2026-09-28)."""
    try:
        info = dict((k, int(v.split()[0])) for k, v in
                    (line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines()))
    except (OSError, ValueError) as e:
        return {"error": f"{type(e).__name__}: {e}"}
    total, avail = info["MemTotal"] / 1048576, info["MemAvailable"] / 1048576
    return {"total_gib": round(total, 1), "used_gib": round(total - avail, 1),
            "free_gib": round(avail, 1), "used_pct": round(100 * (total - avail) / total)}


def system_snapshot(proc=Path("/proc"), sys_root=Path("/sys"), disk_path="/", run=None):
    """CPU, GPU, RAM, disk, temperature and network at this moment, for the Hub's Status card (t209).

    Jas: "I want to see overall CPU, GPU, RAM, SSD, temp, and network data ... a point in time I
    load the page." Every source is a parameter so test_system_snapshot.py can stand fixture files
    in for /proc and /sys and a fake for nvidia-smi. Each section is guarded on its own: one that
    cannot be read becomes {"error": ...} and never hides the others."""
    import glob
    import shutil
    import subprocess

    if run is None:
        def run(cmd):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            except (OSError, subprocess.SubprocessError) as e:
                return 1, f"{type(e).__name__}: {e}"
            return r.returncode, (r.stdout if r.returncode == 0 else (r.stderr or r.stdout))
    proc, sys_root, out = Path(proc), Path(sys_root), {}
    try:
        load1 = float((proc / "loadavg").read_text().split()[0])
        cores = sum(1 for x in (proc / "cpuinfo").read_text().splitlines() if x.startswith("processor"))
        out["cpu"] = {"load1": load1, "cores": cores, "load_pct": round(100 * load1 / cores)}
    except (OSError, ValueError, IndexError, ZeroDivisionError) as e:
        out["cpu"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        info = dict((k, int(v.split()[0])) for k, v in
                    (x.split(":", 1) for x in (proc / "meminfo").read_text().splitlines() if ":" in x))
        total, avail = info["MemTotal"] / 1048576, info["MemAvailable"] / 1048576
        out["ram"] = {"total_gib": round(total, 1), "used_gib": round(total - avail, 1),
                      "used_pct": round(100 * (total - avail) / total)}
    except (OSError, ValueError, KeyError, ZeroDivisionError) as e:
        out["ram"] = {"error": f"{type(e).__name__}: {e}"}
    try:
        u = shutil.disk_usage(disk_path)
        out["disk"] = {"total_gib": round(u.total / 2 ** 30, 1), "used_pct": round(100 * u.used / u.total)}
    except (OSError, ZeroDivisionError) as e:
        out["disk"] = {"error": f"{type(e).__name__}: {e}"}
    temps = []
    for f in glob.glob(str(sys_root / "class" / "thermal" / "thermal_zone*" / "temp")):
        try:
            temps.append(int(Path(f).read_text().strip()) / 1000)
        except (OSError, ValueError) as e:
            print(f"  NOTE: {f} unreadable ({e})", file=sys.stderr)
    out["temp_c"] = max(temps) if temps else None
    try:
        rx = tx = 0
        for line in (proc / "net" / "dev").read_text().splitlines()[2:]:
            name, rest = line.split(":", 1)
            if name.strip() == "lo":
                continue
            f = rest.split()
            rx, tx = rx + int(f[0]), tx + int(f[8])
        out["net"] = {"rx_gib": round(rx / 2 ** 30, 2), "tx_gib": round(tx / 2 ** 30, 2)}
    except (OSError, ValueError, IndexError) as e:
        out["net"] = {"error": f"{type(e).__name__}: {e}"}
    rc, text = run(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits"])
    try:
        if rc:
            raise ValueError(text.strip() or f"exit {rc}")
        util, temp = (int(v) for v in text.strip().splitlines()[0].split(","))
        out["gpu"] = {"util_pct": util, "temp_c": temp}
    except (ValueError, IndexError) as e:
        out["gpu"] = {"error": f"nvidia-smi: {e}"}
    return out


def _proc(pid, name):
    try:
        return Path(f"/proc/{pid}/{name}").read_bytes()
    except OSError:
        return b""


@_memo(10)
def gpu_mib(run=None):
    """{pid: MiB} the GPU driver has allocated to each process (nvidia-smi --query-compute-apps).

    ON THE GB10 THIS IS MOST OF A MODEL (2026-10-08). The GPU allocates from the shared pool, and
    that memory is NOT in VmRSS: Swift's engine showed 3.3 GiB resident while the driver held
    91,815 MiB for it, and the embedder's runner 1.1 GiB resident beside 4,028 MiB. So a model's
    footprint is its resident size PLUS this. Unreadable -> {} (the resident size alone, as before)."""
    out = (run or _run)("nvidia-smi", "--query-compute-apps=pid,used_memory",
                        "--format=csv,noheader,nounits")
    got, unreadable = {}, 0
    for line in (out or "").splitlines():
        try:
            pid, mib = (x.strip() for x in line.split(","))
            got[pid] = got.get(pid, 0) + float(mib)
        except ValueError:
            unreadable += 1                    # "[N/A]" and the like: said below, not counted as zero
    if unreadable:
        print(f"  NOTE: {unreadable} nvidia-smi compute-app row(s) unreadable", file=sys.stderr)
    return got


@_memo(10)
def runner_rss():
    """{(server port, model blob sha): GiB} for every live ollama runner, from /proc.

    `ollama ps` SIZE is not what a model occupies on this unified-memory box: gemma4:26b reports
    2.3 GB while its runner holds ~11 GiB resident (measured 2026-09-28; Chat's t456). A runner's
    parent is its `ollama serve`; station 2's serve is ours, so its environ names port 11435, and any
    serve whose environ we cannot read is the system one on 11434."""
    serve_port, out = {}, {}
    gpu = gpu_mib()
    pids = [d for d in os.listdir("/proc") if d.isdigit()]
    for pid in pids:
        cmd = _proc(pid, "cmdline").replace(b"\0", b" ").decode(errors="replace")
        if cmd.strip().endswith("ollama serve"):
            env = _proc(pid, "environ").decode(errors="replace")
            m = re.search(r"OLLAMA_HOST=[^\0]*:(\d+)", env)
            serve_port[pid] = int(m.group(1)) if m else 11434   # unreadable environ: the system server
    for pid in pids:
        cmd = _proc(pid, "cmdline").replace(b"\0", b" ").decode(errors="replace")
        if "ollama runner" not in cmd and "llama-server" not in cmd:   # this box runs llama-server
            continue
        status = _proc(pid, "status").decode(errors="replace")
        ppid = re.search(r"^PPid:\s*(\d+)", status, re.M)
        rss = re.search(r"^VmRSS:\s*(\d+)", status, re.M)
        port = serve_port.get(ppid.group(1) if ppid else "", 11434)
        for blob in re.findall(r"sha256-([0-9a-f]{64})", cmd):
            if rss:
                out[(port, blob)] = (out.get((port, blob), 0) + int(rss.group(1)) / 1048576
                                     + gpu.get(pid, 0) / 1024)
    return out


def _blob(port, name):
    """The weights blob a model's modelfile names (FROM .../sha256-...), '' if unknown."""
    out = _run("curl", "-s", "--max-time", "10", f"http://localhost:{port}/api/show",
               "-d", json.dumps({"name": name}))
    try:
        m = re.search(r"sha256-([0-9a-f]{64})", json.loads(out).get("modelfile", ""))
    except ValueError:
        return ""
    return m.group(1) if m else ""


def resident():
    rows = []
    rss = runner_rss()
    for label, port in servers():
        out = _run("curl", "-s", "--max-time", "10", f"http://localhost:{port}/api/ps")
        try:
            models = json.loads(out).get("models", []) if out.strip() else []
        except ValueError:
            models = []
        for m in models:
            m = dict(m)
            m["server"], m["port"] = label, port
            b = _blob(port, m.get("name", ""))
            m["rss_gib"] = round(rss[(port, b)], 1) if (port, b) in rss else None
            rows.append(m)
    rows += engine_resident()
    return rows


@_memo(30)
def engine_resident_cheap():
    """[{name, server, port}] of models on non-Ollama engines, from the server list alone -- no
    /proc walk or nvidia-smi, so every job row can be placed on its real server cheaply."""
    rows = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "projects" / "local-inference"))
        import infer
        for sid, s in infer.servers().items():
            if s.entry.get("engine") != "ollama":
                for name in s.loaded():
                    rows.append({"name": name, "server": sid, "port": s.url.rsplit(":", 1)[-1]})
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: engine servers unread ({type(e).__name__}: {e})", file=sys.stderr)
    return rows


@_memo(10)
def engine_held_gib(engine, gpu=None):
    """GiB a non-Ollama engine holds now: resident + GPU-allocated memory of its processes, found by
    their process names (vLLM names its workers VLLM::...). None when nothing matches."""
    gpu = gpu_mib() if gpu is None else gpu
    names = {"vllm": ("VLLM::", "vllm serve")}.get(engine or "", ())
    total, hit = 0.0, False
    for pid in (d for d in os.listdir("/proc") if d.isdigit()):
        cmd = _proc(pid, "cmdline").replace(b"\0", b" ").decode(errors="replace")
        comm = _proc(pid, "comm").decode(errors="replace")
        if not any(n in cmd or n in comm for n in names):
            continue
        rss = re.search(r"^VmRSS:\s*(\d+)", _proc(pid, "status").decode(errors="replace"), re.M)
        total += (int(rss.group(1)) / 1048576 if rss else 0) + gpu.get(pid, 0) / 1024
        hit = True
    return round(total, 1) if hit else None


def engine_resident():
    """Models held by the NON-Ollama engines in SERVERS.json (vLLM serving Swift), via infer.

    Added 2026-10-07: the Lanes tab said "No model is loaded on any server" while Swift ran seven
    jobs on vLLM, because only Ollama's /api/ps was asked. A vLLM server holds its model for as
    long as it is up; its size is the footprint declared on its server entry."""
    rows = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "projects" / "local-inference"))
        import infer
        for sid, s in infer.servers().items():
            if s.entry.get("engine") == "ollama":
                continue
            # Swift's entry says engine "openai" (the API it speaks); the processes are vLLM's.
            held = engine_held_gib("vllm" if "vllm" in (sid + str(s.entry.get("what", ""))).lower()
                                   else s.entry.get("engine"))
            for name, gb in s.loaded().items():
                rows.append({"name": name, "size": gb * 1e9, "server": sid, "port": s.url.rsplit(":", 1)[-1],
                             "rss_gib": held, "expires_at": "while the server runs"})
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: engine servers unread ({type(e).__name__}: {e})", file=sys.stderr)
    return rows


@_memo(30)
def engine_servers():
    """[(label, port)] of the non-Ollama engines answering now (see engine_resident)."""
    out = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "projects" / "local-inference"))
        import infer
        for sid, s in infer.servers().items():
            if s.entry.get("engine") != "ollama" and s.health():
                out.append((sid, s.url.rsplit(":", 1)[-1]))
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: engine servers unread ({type(e).__name__}: {e})", file=sys.stderr)
    return out


def hms(s):
    s = int(s)
    return f"{s//3600}h{(s%3600)//60:02d}m" if s >= 3600 else f"{s//60}m{s%60:02d}s"


def _started(pid):
    """Process start as epoch seconds, from /proc/<pid>/stat field 22.

    NOT the mtime of /proc/<pid>: that is whenever the kernel instantiated the
    directory entry, which on a long job can be the first time anything looked,
    and it made a three-hour lane read as minutes old. Field 22 is the start time
    in clock ticks after boot, which does not move.
    """
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
        ticks = int(fields[19])                     # field 22, 0-indexed after comm
        boot = next(float(l.split()[1]) for l in Path("/proc/stat").read_text().splitlines()
                    if l.startswith("btime"))
        return boot + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def _last_attempt(path):
    """The highest attempt number the log has announced, anywhere in it.

    Not only the last line: between announcements the last line is a refusal or a
    suite result, and a report that only reads the last line says "attempt ?" for
    most of an attempt's life.
    """
    try:
        found = re.findall(r"attempt (\d+)", path.read_text(errors="replace"))
        return int(found[-1]) if found else None
    except OSError:
        return None


QUIET_AFTER = 900
# An attempt is ONE model call, and a call writes nothing to the log until it
# returns. At the measured bf16 rate a maxed attempt takes up to 1h40m, so a log
# silent for 15 minutes is normal mid-attempt. "QUIET" therefore is NOT "wedged" --
# it is "the log cannot vouch for this lane right now; look at the GPU". The page
# says so rather than turning a long attempt red.


def _ppid(pid):
    """This process's parent, from /proc. None when it cannot be read."""
    try:
        for line in (Path("/proc") / str(pid) / "status").read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _scheduled_models():
    """{pid: model} for jobs the scheduler has started and not yet finished.

    WHY THIS IS NEEDED. Only coder.py takes --model on its command line. A script-review or a
    supervision pass is started as `python3 truncation_review.py --path X` and picks its model
    from settings, so reading argv gave "(default)" -- a lane that says a model is busy without
    saying WHICH, on a two-model box where that is the whole question.

    Read straight from the ledger rather than through dispatch_loop: that module imports THIS
    one, and importing it back would be a cycle.
    """
    out = {}
    # Parsed once per process, then only appended lines (jsonl_cache, 2026-10-03): the ledger is
    # 67 MB and the Hub's Lanes and Misc pages each re-read it whole -- ~20 s a load. A torn line
    # is skipped rather than throwing away every row, as the old json.loads-in-a-list did.
    import jsonl_cache
    rows = jsonl_cache.rows(REPO / "workspace" / "dispatch-loop.jsonl")
    done = {r.get("pid") for r in rows if r.get("event") == "finished"}
    for r in rows:
        if r.get("event") == "started" and r.get("pid") not in done and r.get("model"):
            out[r["pid"]] = r["model"]
    return out


def _scheduled_roles():
    """{pid: role} for jobs the scheduler started, from the role its ledger row names."""
    import jsonl_cache
    rows = jsonl_cache.rows(REPO / "workspace" / "dispatch-loop.jsonl")
    done = {r.get("pid") for r in rows if r.get("event") == "finished"}
    return {r["pid"]: r["role"] for r in rows
            if r.get("event") == "started" and r.get("pid") not in done and r.get("role")}


def role_of(pid, argv, kind, scheduled_roles, d=None):
    """THE ROLE A LANE BELONGS TO (Jas, 2026-10-08: "I see a mis match in the role tiles, scheduler
    areas, and the running jobs"). The tiles, the scheduler's list and Running jobs all read lanes from
    here, so they agree by construction. In order: the scheduler's ledger row for this job or an
    ancestor; ask_q16's own --role (the scoping workers pass it); the role whose list has the kind."""
    seen = pid
    for _ in range(4):
        if seen in scheduled_roles:
            return scheduled_roles[seen]
        seen = _ppid(seen)
        if not seen or seen == 1:
            break
    named = arg(argv, "--role", "")
    if named:
        return named
    try:
        import task_routing as T
        d = d or T.load()
        # THE ROLE IS LOOKED UP, NOT NAMED: this line used to answer "manager", which was both the
        # kind and a role key. The kind is "scoping" now, and the ROLE that owns it is whatever the
        # store says -- delete that role and make a new one holding Scoping and this follows.
        k = (T.owner(d, "scoping") or kind) if kind == "scoping" and "manager:" in arg(argv, "--purpose", "") else kind
        # A BUILD BELONGS TO THE ROLE SERVING ITS MODEL (2026-10-18). Building's owner is one role, but
        # each building role builds on its own model: this returned the Manager for the Auditor's Q4
        # build on the eGPU, so the Production tab showed it under the Manager's tile.
        model = arg(argv, "--model", "")
        if kind == "production" and model:
            task = T.KIND_TASK.get(T.BUILD, T.BUILD)     # membership, not position(): an empty list
            mine = [r for r in T.model_roles(d) if d["roles"][r].get("model") == model     # gives 0
                    and any(t.get("task") == task for t in T.tasks_of(d, r))]
            return min(mine, key=lambda r: T.position(d, r, T.BUILD)) if mine else None
        return T.owner(d, k) or T.owner(d, kind)
    except Exception as e:                                          # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: role of pid {pid} unknown ({type(e).__name__}: {e})", file=sys.stderr)
        return None


def model_of(pid, argv, scheduled):
    """The model this lane is using: from argv when it says, else from what started it.

    THE LEDGER PID IS NOT THE LANE PID. dispatch_loop records the child it spawned and this
    module sees the python process itself, which is a GRANDchild -- measured 2026-09-16,
    lane 2568083 against ledger 2568071. So walk up the parents until one of them is a job
    the scheduler started. Bounded at 4 hops: an answer this cannot find quickly is one it
    should not guess at.
    """
    named = arg(argv, "--model", "")
    if named:
        return named
    seen = pid
    for _ in range(4):
        if seen in scheduled:
            return scheduled[seen]
        seen = _ppid(seen)
        if not seen or seen == 1:
            break
    role = SCRIPT_ROLE.get(script_of(argv))
    if role:
        try:
            import roles
            return roles.tag(role, "(default)")
        except ImportError as e:
            print(f"  NOTE: roles.py not importable ({e})", file=sys.stderr)
    return "(default)"


_REMOTE = None


def _remote_of():
    """{model tag: (server id, port)} for models SERVERS.json pins to a server on another device."""
    global _REMOTE
    if _REMOTE is None:
        _REMOTE = {}
        try:
            srv = json.loads((REPO / "projects" / "local-inference" / "models" / "SERVERS.json")
                             .read_text(encoding="utf-8"))
            for sid, x in (srv.get("servers") or {}).items():
                if x.get("device", "gx10") != "gx10" and x.get("url"):
                    port = int(x["url"].rstrip("/").rsplit(":", 1)[-1])
                    for tag in (x.get("models") or {}):
                        _REMOTE[tag] = (sid, port)
        except (OSError, ValueError) as e:
            print(f"  NOTE: SERVERS.json unread ({type(e).__name__}); remote models unlabelled",
                  file=sys.stderr)
    return _REMOTE


def snapshot():
    """Everything the text report and the Status tab show, as data. One source for both."""
    now = time.time()
    rate = rates()
    recent = rates_recent(now=now)
    engine_of = {r["name"]: (r["server"], int(r["port"])) for r in engine_resident_cheap()}
    scheduled = _scheduled_models()
    sched_roles = _scheduled_roles()
    out = []
    for L in lanes():
        argv, pid = L["argv"], L["pid"]
        model = model_of(pid, argv, scheduled)
        path, mtime, last = live_log(pid)
        start = _started(pid)
        row = {
            "pid": pid,
            # WHAT KIND OF WORK, because "a model is busy" is not the same question as
            # "production is moving", and a Status tab that cannot tell them apart makes a
            # box running three script-reviews look identical to one building a feature.
            "kind": L.get("kind", "production"),
            "script": L.get("script", "coder.py"),
            "state": L["stat"],
            "elapsed_s": round(now - start) if start else None,
            "model": model,
            "role": role_of(pid, argv, L.get("kind", "production"), sched_roles),
            # --brief-file FIRST. Since t263 every scheduled job passes the brief as a file,
            # and reading only --brief reported "(none)" for all of them -- which the
            # scheduler reads as "this brief is not running" and would have started the
            # same chunk again on the next free model. Found on the first real tick.
            "brief": Path(arg(argv, "--brief-file", "") or arg(argv, "--brief", "(none)")).name,
            # --path is truncation_review's subject and --failure is supervise's; without
            # these a script-review lane showed "(none)" for everything it was doing.
            "files": arg(argv, "--files", "") or arg(argv, "--path", "")
                     or arg(argv, "--failure", "(none)"),
            "target": arg(argv, "--target", "(none)"),
            "log": str(path) if path else None,
            "log_shared": path == LEGACY_LIVE,
            "log_age_s": round(now - mtime) if mtime else None,
            "log_growing": bool(mtime) and (now - mtime) < QUIET_AFTER,
            "last_line": last,
            "attempt": _last_attempt(path) if path else None,
            "args": " ".join(argv[2:]),
            # WHICH OLLAMA SERVER THIS JOB TALKS TO (station 2, 2026-09-28): the manager queue sets
            # HIVEMIND_OLLAMA for its own Q4 copy. Without this a station-2 job read as occupying the
            # main server's copy of the same model, and the dispatcher queued 112 priority jobs
            # behind it while the main copy sat idle.
            "server_port": _server_port(pid),
        }
        # A MODEL ON A NON-OLLAMA ENGINE IS SERVED THERE (2026-10-08): Swift's jobs read "main :11434"
        # because only HIVEMIND_OLLAMA was consulted; vLLM serves them on :8000.
        if model in engine_of:
            row["server_name"], row["server_port"] = engine_of[model]
        # A MODEL PINNED TO ANOTHER DEVICE IS SERVED THERE (2026-10-13): the ThinkPad's Q4 read
        # "main :11434" on the Lanes tab, because only this box's ports and HIVEMIND_OLLAMA were read.
        elif model in _remote_of():
            row["server_name"], row["server_port"] = _remote_of()[model]
        if model in recent:
            rmed, rn, rage = recent[model]
            row.update(recent_rate=round(rmed, 1), recent_calls=rn, recent_newest_s=round(rage))
        if L.get("script") == "ask_q16.py":
            row["brief"] = arg(argv, "--purpose", "") or Path(arg(argv, "--prompt", "(none)")).name
            # ask_q16 drives every role's long calls, not only the Manager's (2026-10-13: RCA on the
            # ThinkPad showed as "manager"). Its prompt directory says whose work it is.
            if str(row["brief"]).startswith("script audit"):
                # The script audit reads sessions through session_review's reader, so its prompt sits
                # in a session-review temp dir; it is the Manager's script_audit, not a session review
                # (2026-10-13, Jas: "The manager tile says session reviews but that isn't a task in its config").
                row["kind"] = "script-audit"
            elif "session-review" in arg(argv, "--prompt", ""):
                row["kind"] = "rca" if str(row["brief"]).startswith("RCA") else "session-review"
            elif "manager-queue" in arg(argv, "--prompt", ""):
                row["kind"] = "scoping"
        if model in rate:
            med, lo, hi, n = rate[model]
            row.update(rate_median=round(med, 2), rate_lo=round(lo, 2),
                       rate_hi=round(hi, 2), rate_runs=n,
                       attempt_s_median=round(16000 / med),
                       attempt_s_lo=round(16000 / hi), attempt_s_hi=round(16000 / lo))
        out.append(row)
    seen = {}
    for r in out:
        seen.setdefault(r["args"], []).append(r["pid"])
    for r in out:
        r["duplicate_of"] = [p for p in seen[r["args"]] if p != r["pid"]]
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "lanes": out,
            "memory": memory(), "servers": [{"name": n, "port": p} for n, p in servers() + engine_servers()],
            "gpu": gpu(), "resident": [
                {"name": m.get("name"), "gb": round(m.get("size", 0) / 1e9, 1),
                 "server": m.get("server"), "port": m.get("port"), "rss_gib": m.get("rss_gib"),
                 "expires": m.get("expires_at") or ""} for m in resident()]}


def main():
    if "--json" in sys.argv:
        print(json.dumps(snapshot(), indent=1))
        return 0
    d = snapshot()
    print("=" * 70)
    print(" LANE STATUS — local model dispatches running right now")
    print("=" * 70)
    bp = build_progress()
    if bp:
        eta = hms(bp["eta_s"]) if bp["eta_s"] is not None else "unknown"
        print(f"\n  BUILDS TODAY  {bp['done']}/{bp['total']} done ({bp['accepted']} accepted)"
              f" · {bp['running']} running · {bp['waiting']} waiting · ETA ~{eta}")
        print(f"                median build {hms(bp['median_s'] or 0)} over {bp['builds_measured']} "
              f"builds; not counted: {bp['held']} held/parked (need a person), "
              f"{bp['review']} delivered awaiting review")
        if bp.get("other_queued"):
            print("  ALSO QUEUED   " + ", ".join(f"{n} {k}" for k, n in
                                                sorted(bp["other_queued"].items())))
    sweep = sweep_progress()
    if sweep:
        print(f"  TEST SWEEP    {sweep}")
    print_manager(d)
    if not d["lanes"]:
        print("\n  No coder.py dispatch is running.")
    for r in d["lanes"]:
        el = hms(r["elapsed_s"]) if r["elapsed_s"] is not None else "?"
        where = r.get("server_name") or SERVER_NAMES.get(r.get("server_port"), f"port {r.get('server_port')}")
        print(f"\n  pid {r['pid']}   {el} elapsed   state={r['state']}   server={where}")
        for k in ("model", "brief", "files", "target"):
            print(f"    {k:6s} {r[k]}")
        if r["log"] is None:
            print("    log    NONE — cannot tell whether this lane is moving.")
        else:
            tag = "GROWING" if r["log_growing"] else "QUIET (normal mid-attempt; check GPU)"
            print(f"    log    {r['log']}  last write {hms(r['log_age_s'])} ago  [{tag}]")
            if r["log_shared"]:
                print("           (shared pre-2026-09-14 log: other lanes' lines are mixed in)")
            print(f"    last   {r['last_line'][:100]}")
            if r["attempt"]:
                print(f"    on     attempt {r['attempt']}")
        if "recent_rate" in r:
            print(f"    now    {r['recent_rate']:.1f} tok/s -- median of {r['recent_calls']} call(s) "
                  f"that finished in the last {RECENT_S // 60} min (newest {hms(r['recent_newest_s'])} ago)")
        else:
            print(f"    now    no call on this model finished in the last {RECENT_S // 60} min")
        if "rate_median" in r:
            print(f"    rate   {r['rate_median']:.1f} tok/s median decode "
                  f"({r['rate_lo']:.1f}–{r['rate_hi']:.1f} over {r['rate_runs']} run(s) "
                  f"that decoded >{MIN_DECODE_SAMPLE} tokens)")
            print(f"    a maxed 16,000-token attempt costs ~{hms(r['attempt_s_median'])} "
                  f"(range {hms(r['attempt_s_lo'])}–{hms(r['attempt_s_hi'])})")
        else:
            print(f"    rate   no run for {r['model']} decoded enough tokens to measure")
        if r["duplicate_of"]:
            print(f"    ** DUPLICATE of pid {r['duplicate_of']}: identical arguments, same GPU,"
                  f" and with --apply the same branch.")
    print("\n  GPU  (name, load, power, temperature C)")
    for g in d["gpu"] or ["(nvidia-smi unavailable)"]:
        print(f"    {g}")
    th = thermal()
    if th:
        hot, crit, n = th
        room = f"{crit - hot:.0f} C below the {crit:.0f} C shutdown" if crit else "no trip point read"
        flag = "  ** HOT **" if crit and crit - hot < 15 else ""
        print(f"    system: hottest of {n} sensors {hot:.1f} C -- {room}{flag}")
    else:
        print("    system: temperature unreadable (/sys/class/thermal)")
    mem = d["memory"]
    if "error" in mem:
        print(f"\n  MEMORY  unreadable: {mem['error']}")
    else:
        print(f"\n  MEMORY  {mem['used_gib']} GiB used · {mem['free_gib']} GiB free of {mem['total_gib']} GiB"
              f" ({mem['used_pct']}%) -- shared by CPU and GPU on this box")
    print(f"  Ollama servers: " + (", ".join(f"{x['name']} (:{x['port']})" for x in d["servers"]) or "none answering"))
    print("\n  Models resident in memory  (ollama's size | the runner process's real resident memory)")
    for m in d["resident"]:
        real = f"{m['rss_gib']} GiB resident" if m.get("rss_gib") is not None else "resident: unreadable"
        print(f"    [{m.get('server')}] {m['name']}  ollama says {m['gb']} GB | {real}  "
              f"expires {m['expires']}")
    if not d["resident"]:
        print("    (ollama reported none — nothing is loaded)")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())


def remote_devices(now=None, timeout=4):
    """Every device other than the GX10 (Jas, 2026-10-13: "can the ThinkPad jobs be shown on the
    Production tab? ... its stats on the Lanes tab"). servers() finds only ports on THIS box, so the
    ThinkPad eGPU was invisible on both tabs while it did all script review, RCA and session review.

    Per device: reachable, VRAM in use against its budget (Ollama's /api/ps size_vram -- its own
    memory, not the GX10's pool), the models loaded, jobs running (the scheduler's ledger, by model)
    and jobs finished in the last 24 h by kind, with failures and median minutes. Read live; an
    unreachable device says so rather than showing an empty table."""
    import urllib.request
    base = REPO / "projects" / "local-inference" / "models"
    try:
        srv = json.loads((base / "SERVERS.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [{"device": "?", "error": f"SERVERS.json unreadable: {type(e).__name__}: {e}"}]
    sys.path.insert(0, str(REPO / "scripts"))
    import dispatch_loop as DL
    history = DL.rows()
    running = DL.ledger_running(history)
    now = now or time.time()
    out = []
    for dev, dv in (srv.get("devices") or {}).items():
        if dev == "gx10":
            continue
        sids = [k for k, s in (srv.get("servers") or {}).items() if s.get("device") == dev]
        d = {"device": dev, "what": dv.get("what"), "memory_gb": dv.get("memory_gb"),
             "reserve_gb": dv.get("reserve_gb"), "servers": sids, "loaded": [], "running": [],
             "done": {}, "reachable": False}
        tags = set()
        for sid in sids:
            s = srv["servers"][sid]
            tags |= set((s.get("models") or {}))
            try:
                with urllib.request.urlopen(s["url"].rstrip("/") + "/api/ps", timeout=timeout) as f:
                    ps = json.load(f).get("models") or []
                d["reachable"] = True
                for m in ps:
                    tags.add(m.get("name"))
                    d["loaded"].append({"server": sid, "name": m.get("name"),
                                        "vram_gb": round((m.get("size_vram") or 0) / 1e9, 2),
                                        "context": m.get("context_length"),
                                        "until": m.get("expires_at")})
            except Exception as e:                                  # noqa: BLE001 -- said on the page
                d["error"] = f"{sid}: {type(e).__name__}: {e}"
        d["vram_used_gb"] = round(sum(m["vram_gb"] for m in d["loaded"]), 2)
        started = {r.get("pid"): r for r in history if r.get("event") == "started"}
        for r in running:
            if r.get("model") in tags:
                st = started.get(r.get("pid")) or {}
                age = now - _ts(st["at"]) if st.get("at") else None
                d["running"].append({"kind": r.get("kind"), "key": r.get("key"), "model": r.get("model"),
                                     "pid": r.get("pid"), "seconds": int(age) if age is not None else None})
        for r in history:
            if r.get("event") != "finished" or r.get("model") not in tags or not r.get("at"):
                continue
            if now - _ts(r["at"]) > 86400:
                continue
            k = d["done"].setdefault(r.get("kind") or "?", {"n": 0, "failed": 0, "secs": []})
            k["n"] += 1
            k["failed"] += 0 if r.get("exit") == 0 else 1
            if r.get("seconds") is not None:
                k["secs"].append(r["seconds"])
        for k in d["done"].values():
            k["median_min"] = round(_median(k.pop("secs")) / 60, 1) if k["secs"] else None
        out.append(d)
    return out


def embedder_stats(window_s=3600, now=None, calls=None):
    """The embedder's tile: where it is loaded and what it did in the last hour (memory._log_call)."""
    now = now or time.time()
    path = Path(calls or (REPO / "workspace" / "run-logs" / "embed-calls.jsonl"))
    n = texts = failed = torn = tok = tok_s = 0
    secs = []
    if path.exists():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-5000:]:
            try:
                r = json.loads(line)
            except ValueError:
                torn += 1                       # a half-written last line; counted and returned
                continue
            t = _ts(r.get("at"))
            if t is None or now - t > window_s:
                continue
            n += 1
            texts += r.get("texts") or 0
            failed += 0 if r.get("ok") else 1
            if r.get("seconds") is not None:
                secs.append(r["seconds"])
                if r.get("tokens"):
                    tok += r["tokens"]
                    tok_s += r["seconds"]
    return {"calls": n, "texts": texts, "failed": failed, "torn_lines": torn, "tokens": tok,
            "tok_per_s": round(tok / tok_s) if tok_s else None,
            "median_s": round(_median(secs), 2) if secs else None}



def corpus_backlog():
    """What the corpus index has not caught up with (Jas, 2026-10-13: "add a corpus index backlog to the
    production tab"). Two queues, both read from disk:
      transcripts -- session files changed since transcript-index.jsonl was last rebuilt (corpus-index.timer,
                     hourly, keyword index rebuilt whole);
      records     -- memory records whose embedding is missing or stale (memory.build_index's own rule).
    Plus when the index last ran and the timer's next run."""
    sys.path.insert(0, str(REPO / "scripts"))
    import memory
    import transcripts
    idx = transcripts.INDEX
    built = idx.stat().st_mtime if idx.exists() else 0
    stale = [(p, origin) for p, origin, _w in transcripts.sources() if p.stat().st_mtime > built]
    try:
        cache = json.loads(memory.INDEX.read_text()) if memory.INDEX.is_file() else {}
    except ValueError as e:
        cache = {}
        print(f"  NOTE: memory index unreadable ({e}); every record counts as unembedded", file=sys.stderr)
    recs = [r for r in memory.load() if r.get("id") and "_unreadable" not in r
            and cache.get(r["id"], {}).get("sha") != memory.digest(r)]
    nxt, running = None, False
    try:
        # list-timers, not `show -p NextElapseUSecRealtime`: this timer is OnUnitActiveSec (monotonic),
        # so the realtime property is empty.
        out = subprocess.run(["systemctl", "--user", "list-timers", "corpus-index.timer", "--no-legend"],
                             capture_output=True, text=True, timeout=10).stdout.split()
        nxt = " ".join(out[1:3]) if len(out) > 3 and out[0] != "-" else None
        running = subprocess.run(["systemctl", "--user", "is-active", "corpus-index.service"],
                                 capture_output=True, text=True, timeout=10).stdout.strip() == "activating"
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  NOTE: corpus-index.timer unread ({type(e).__name__})", file=sys.stderr)
    return {"transcripts": [{"session": p.stem, "origin": o, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                                time.gmtime(p.stat().st_mtime))}
                            for p, o in stale],
            "records": [{"id": r["id"], "fact": r.get("fact", "")} for r in recs],
            "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(built)) if built else None,
            "next_run": nxt, "running": running}
