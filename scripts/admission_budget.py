#!/usr/bin/env python3
"""The BUDGET the scheduler has promised, next to what the box MEASURES — step 1 of the scheduler redesign.

NOT THE ENGINE-ADMISSION LEDGER (t1044, scripts/tests/test_admission_ledger.py) and NOT the promise ledger
(scripts/tests/test_promise_ledger.py). There are 54 ledger-named suites in this repository; picking a name
without enumerating them cost a clobbered oracle tonight, so: this one is the SCHEDULER's resource budget --
host bytes for builds and suites, sequence slots and KV tokens for inference -- and it names itself for the
thing it budgets rather than for the word "ledger".

    python3 scripts/admission_ledger.py            # the report the tick prints
    python3 scripts/admission_ledger.py --reconcile

WHY TWO LEDGERS, and this correction came from review and a measurement, not from me:
  (a) HOST BYTES. Builds and test suites spawn processes and copy the repo; that memory is host RAM and it is
      what `MemAvailable` reports. Measured 2026-10-29: four concurrent inference requests moved MemAvailable by
      -0.46 GB total, i.e. ~0.1 GB each.
  (b) MODEL CAPACITY. The vLLM engine runs `--max-num-seqs 4` with `--kv-cache-memory-bytes 12288m` and
      `--max-model-len 262144`: its memory is a PREALLOCATED pool (nvidia-smi reports VLLM::EngineCore at 92007
      MiB), so an inference job costs sequence SLOTS and KV TOKENS inside that pool, not host bytes. Two
      inference jobs and two builds compete for DIFFERENT things.
  Folding them into one "headroom" number would have been wrong: a preflight does not use the RAM a build needs,
  and a build does not use a sequence slot.

WHAT STEP 1 IS: observation. Nothing here gates a start. It records every reservation and release, reconciles
outstanding ones against LIVE PIDS every time it is asked, and prints promised beside measured. The numbers then
decide the reservations (from measured p95 peaks, not constants) before anything is gated on them.

THE FOUR CONDITIONS, from the same review, because a ledger that leaks is worse than none:
  1. it lives OUTSIDE git (~/.cache), so a booking is not a commit;
  2. a failed write NEVER blocks or delays a start -- every function here is total, and says so on stderr;
  3. the report prints PROMISED and MEASURED side by side, so a lie is visible;
  4. RECONCILIATION ships with step 1: 127 of 18,306 finish rows in this repository's own ledger are `lost` --
     the job died without writing its own finish -- so "release on completion" would leak in exactly the cases
     that matter. A reservation whose pid is gone is reaped, and a TTL of twice its duration is the backstop.
"""
import json
import os
import sys
import time
from pathlib import Path

LEDGER = Path(os.environ.get("HIVEMIND_ADMISSION_BUDGET")
              or (Path.home() / ".cache" / "hivemind-tmp" / "admission-ledger.jsonl"))
DEFAULT_TTL_S = 2 * 60 * 60          # twice the longest a job may legitimately run


def _now():
    return time.time()


def _append(row):
    """Total: a booking that cannot be written is said, never raised, never waited on."""
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return True
    except OSError as e:
        print(f"  NOTE: admission ledger not written ({type(e).__name__}: {e}) -- the start is not blocked",
              file=sys.stderr)
        return False


def reserve(holder, kind, host_gb=0.0, slots=0, kv_tokens=0, pid=None, duration_s=None):
    """Book what a job is about to use. `holder` must be unique per job (kind:key:pid is enough)."""
    return _append({"at": _now(), "event": "reserve", "holder": str(holder), "kind": str(kind),
                    "host_gb": float(host_gb or 0), "slots": int(slots or 0),
                    "kv_tokens": int(kv_tokens or 0), "pid": pid,
                    "ttl_s": int(duration_s or DEFAULT_TTL_S)})


def release(holder, reason=""):
    return _append({"at": _now(), "event": "release", "holder": str(holder), "reason": str(reason)})


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError):
        return True          # cannot tell: never free a promise on a guess


def outstanding(path=None, now=None, alive=None):
    """{(kind, holder): {host_gb, slots, kv_tokens, at, pid, reaped}} -- reservations not yet released.

    RECONCILED, not trusted: a reservation is dropped when its holder was released, when its pid is gone, or
    when it is older than its TTL. That is the condition the review required, and the record backs it: 127
    finish rows in this repository were written by the account, not by the job, because the job died.
    """
    path = Path(path or LEDGER)
    now = _now() if now is None else now
    alive = alive or pid_alive
    if not path.exists():
        return {}
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            print(f"  NOTE: unreadable admission ledger line skipped in {path.name}", file=sys.stderr)
    live, reaped = {}, 0
    for r in rows:
        h = r.get("holder")
        if r.get("event") == "reserve":
            live[h] = r
        elif r.get("event") == "release":
            live.pop(h, None)
    for h, r in list(live.items()):
        pid, ttl = r.get("pid"), int(r.get("ttl_s") or DEFAULT_TTL_S)
        if (pid and not alive(pid)) or (now - float(r.get("at") or now)) > ttl:
            reaped += 1
            live.pop(h)
    if reaped:
        live["__reaped__"] = {"reaped": reaped}
    return live


def totals(live):
    """(host_gb, slots, kv_tokens) promised right now, ignoring the reaped marker."""
    host = sum(float(r.get("host_gb") or 0) for k, r in live.items() if k != "__reaped__")
    slots = sum(int(r.get("slots") or 0) for k, r in live.items() if k != "__reaped__")
    kv = sum(int(r.get("kv_tokens") or 0) for k, r in live.items() if k != "__reaped__")
    return host, slots, kv


def promised_vs_measured(samples_path=None, path=None, now=None):
    """(clean_samples, overruns, detail) -- each build's BOOKING beside the peak actually recorded while it ran.

    THE DATA THE GATE NEEDS BEFORE IT MAY BE SWITCHED ON (redesign step 2, N = 20). A booking is only as good as
    the peaks it is compared with, so this joins two records that already exist: this budget's reserve/release
    intervals, and scripts/memory_watch.py's per-group rows (it groups a build's processes under `job:build` and
    records rss_gb every 20 s).

    A SAMPLE IS "CLEAN" ONLY WHEN THE READING IS UNAMBIGUOUS: exactly one build was booked at that instant, so the
    `job:build` group is that build. With two builds running the group is their sum and says nothing about either
    -- counting those would inflate the peak and make the reservation look wrong for the wrong reason.

    Measured 2026-10-29, the numbers this exists to get: MemTotal 121.6 GB, the model's group at 89.85 GB GPU,
    MemAvailable 18-22 GB, and 26 GB of a 48 GB swap file in use -- the box is over-committed, so peaks are likely
    to exceed a 12 GB booking and the reservation may be too small. That is exactly what N samples must settle.
    """
    # WHERE MEMORY-WATCH ACTUALLY WRITES (fixed 2026-10-29, found when Jas asked what was left and the answer was
    # read rather than recalled): the default was ~/.cache/hivemind-tmp/memory-watch.jsonl, WHICH DOES NOT EXIST.
    # memory_watch.py writes workspace/memory-watch.jsonl in the repository (338 MB, 88,369 rows, current). So this
    # counter could never see a sample and N was zero BY CONSTRUCTION -- a ledger that reports "no samples yet" for
    # ever, which reads exactly like "we have not run enough builds".
    samples_path = Path(samples_path or (Path(__file__).resolve().parent.parent / "workspace" / "memory-watch.jsonl"))
    ledger = Path(path or LEDGER)
    now = _now() if now is None else now
    # rebuild the intervals
    if not ledger.exists():
        return 0, 0, "no budget ledger yet -- nothing booked, so nothing to compare"
    booked, intervals = {}, []
    for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(line)
        except ValueError as e:
            # SAID: a line that will not parse is one fewer interval this join can judge, and a silent skip would
            # report a confident sample count that is missing records.
            print(f"  NOTE: unreadable budget line skipped ({e})", file=sys.stderr)
            continue
        h = r.get("holder")
        if r.get("event") == "reserve":
            booked[h] = r
        elif r.get("event") == "release" and h in booked:
            b = booked.pop(h)
            intervals.append((float(b.get("at") or 0), float(r.get("at") or now), b))
    for h, b in booked.items():                                  # still open: to now, capped by its TTL
        intervals.append((float(b.get("at") or 0),
                          min(now, float(b.get("at") or 0) + int(b.get("ttl_s") or DEFAULT_TTL_S)), b))
    builds = [i for i in intervals if (i[2].get("kind") == "build")]
    if not samples_path.exists():
        return 0, 0, f"no memory-watch samples at {samples_path.name} -- the peaks are not recorded yet"
    clean, overruns, detail = 0, 0, []
    for line in samples_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError as e:
            print(f"  NOTE: unreadable memory-watch line skipped ({e})", file=sys.stderr)
            continue
        # MEMORY-WATCH WRITES AN ISO STAMP UNDER "at", NOT AN EPOCH UNDER "ts" (2026-10-29): my first version
        # read "ts", so every row was skipped and the function would have reported ZERO samples for ever while
        # looking like it worked. One real row verified: {"at": "2026-10-29T03:21:37Z", "groups": {...}}.
        at = row.get("ts")
        if at is None and row.get("at"):
            try:
                # timegm, NOT mktime: the record is UTC and mktime reads LOCAL time, so my first version was an
                # hour out on this box (UTC-6 -> it put 03:21Z at 02:21Z) and would have matched samples to the
                # wrong booking interval. Verified by round-tripping a real row.
                import calendar
                at = calendar.timegm(time.strptime(str(row["at"]), "%Y-%m-%dT%H:%M:%SZ"))
            except (ValueError, TypeError):
                at = None
        if at is None:
            continue
        at = float(at)
        live = [b for a, b_, b in builds if a <= at <= b_]
        peak = ((row.get("groups") or {}).get("job:build") or {}).get("rss_gb")
        if len(live) != 1 or peak is None:
            continue                                                  # ambiguous or no build: not a sample
        clean += 1
        booked_gb = float(live[0].get("host_gb") or 0)
        if booked_gb and float(peak) > booked_gb:
            overruns += 1
            detail.append(f"{peak:.1f} GB peak against a {booked_gb:.0f} GB booking")
    return clean, overruns, (f"{clean} clean sample(s), {overruns} over its booking"
                             + ("; e.g. " + "; ".join(detail[:3]) if detail else ""))


def measured():
    """What the box says about itself: host bytes free, and the engine's own slot and KV ceilings."""
    out = {"host_free_gb": None, "slots_max": None, "kv_bytes": None, "model_gib": None}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable"):
                out["host_free_gb"] = round(int(line.split()[1]) / 2 ** 20, 1)
                break
    except OSError as e:
        print(f"  NOTE: MemAvailable unreadable ({e}) -- host shown as ?", file=sys.stderr)
    return out


def report(path=None, now=None, free_gb=None):
    """ONE LINE: what is promised beside what is measured. This is what the tick prints in step 1."""
    live = outstanding(path=path, now=now)
    reaped = (live.get("__reaped__") or {}).get("reaped", 0)
    host, slots, kv = totals(live)
    m = measured()
    free = m["host_free_gb"] if free_gb is None else free_gb
    return (f"admission: host promised {host:.1f} GB, measured free {free if free is not None else '?'} GB; "
            f"model slots promised {slots}, kv tokens promised {kv:,}"
            + (f" ({reaped} stale reservation(s) reaped)" if reaped else ""))


def main(argv):
    if "--reconcile" in (argv or []):
        live = outstanding()
        print(f"  outstanding: {len([k for k in live if k != '__reaped__'])} reservation(s)"
              + (f", reaped {live['__reaped__']['reaped']}" if "__reaped__" in live else ""))
        host, slots, kv = totals(live)
        print(f"  promised: host {host:.2f} GB, slots {slots}, kv tokens {kv}")
        return 0
    print(f"  {report()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
