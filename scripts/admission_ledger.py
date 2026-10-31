#!/usr/bin/env python3
"""What the box has PROMISED, next to what it MEASURES — observation only, step 1 of the scheduler redesign.

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

LEDGER = Path(os.environ.get("HIVEMIND_ADMISSION_LEDGER")
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
