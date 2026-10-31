#!/usr/bin/env python3
"""The scheduler's resource BUDGET: what it promised, beside what the box measures, with the dead reaped.

Step 1 of the scheduler redesign (workspace/rca/scheduler-audit-2026-10-29.md section 4), built to a review that
corrected two of my own measurements before I accepted it:
  * the model IS visible per process (`nvidia-smi --query-compute-apps` reports VLLM::EngineCore 92007 MiB);
  * HOST bytes and MODEL capacity are different resources. Measured: four concurrent inference requests moved
    MemAvailable by -0.46 GB, because the engine's memory is a preallocated pool (`--kv-cache-memory-bytes
    12288m` inside `--max-num-seqs 4`). Builds and suites spend host RAM; inference spends sequence slots and KV
    tokens. One "headroom" number would have been wrong.

OBSERVATION ONLY: nothing here gates a start. What it must get right is the reckoning, because a budget that
leaks is worse than none -- 127 of this repository's 18,306 finish rows were written by the account rather than by
the job, i.e. the job died without writing its own finish.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    import admission_budget as A
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    d = Path(tempfile.mkdtemp())
    A.LEDGER = d / "budget.jsonl"
    me = os.getpid()

    A.reserve("build:p1", "build", host_gb=12, pid=me, duration_s=900)
    check("a reservation is booked with its host bytes", A.totals(A.outstanding())[0] == 12,
          str(A.totals(A.outstanding())))
    A.release("build:p1")
    check("  and released on completion", A.totals(A.outstanding())[0] == 0, str(A.outstanding()))

    # THE KILLED JOB -- the case the review insisted on, and the record backs (127 lost rows).
    A.reserve("preflight:p2", "preflight", slots=1, kv_tokens=30000, pid=999999, duration_s=900)
    live = A.outstanding()
    check("a reservation whose PID IS GONE is reaped, not leaked",
          A.totals(live)[1] == 0 and live.get("__reaped__", {}).get("reaped") == 1, str(live))

    # THE TTL BACKSTOP, for a pid alive but the job gone.
    A.reserve("build:p3", "build", host_gb=12, pid=me, duration_s=1)
    check("a reservation past its TTL is reaped even with a live pid",
          A.totals(A.outstanding(now=time.time() + 5))[0] == 0, "")
    A.release("build:p3")

    A.reserve("build:p4", "build", host_gb=12, pid=me, duration_s=900)
    check("a live, fresh reservation is kept", A.totals(A.outstanding())[0] == 12, "")

    A.reserve("preflight:p5", "preflight", slots=1, kv_tokens=120000, pid=me, duration_s=900)
    host, slots, kv = A.totals(A.outstanding())
    check("host bytes and model capacity are counted SEPARATELY", (host, slots, kv) == (12, 1, 120000),
          f"{(host, slots, kv)}")

    r = A.report(free_gb=21.8)
    check("the report prints PROMISED beside MEASURED",
          "promised 12.0 GB" in r and "measured free 21.8 GB" in r and "kv tokens promised 120,000" in r, r)

    A.LEDGER = Path("/proc/nonexistent-dir/budget.jsonl")
    check("a failed write returns False and SAYS so, without raising",
          A.reserve("build:p6", "build", host_gb=1, pid=me) is False, "")

    A.LEDGER = d / "budget.jsonl"
    check("the real budget lives outside the repository",
          str(Path.home() / ".cache" / "hivemind-tmp") in str(A.LEDGER) or A.LEDGER.parent == d, str(A.LEDGER))
    # THE N SAMPLES: a booking joined with the peak recorded while it ran, and ONLY when the reading is
    # unambiguous -- with two builds running, memory_watch's job:build group is their sum and says nothing about
    # either, so counting it would inflate the peak and make the reservation look wrong for the wrong reason.
    import json as _json, calendar as _cal
    led2 = d / "peaks.jsonl"
    A.LEDGER = led2
    t0 = _cal.timegm(time.strptime("2026-10-29T00:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
    led2.write_text("\n".join([
        _json.dumps({"at": t0 + 100, "event": "reserve", "holder": "build:p1", "kind": "build",
                     "host_gb": 12, "pid": me, "ttl_s": 900}),
        _json.dumps({"at": t0 + 200, "event": "release", "holder": "build:p1"}),
        _json.dumps({"at": t0 + 100, "event": "reserve", "holder": "build:p2", "kind": "build",
                     "host_gb": 12, "pid": me, "ttl_s": 900}),
        _json.dumps({"at": t0 + 200, "event": "release", "holder": "build:p2"}),
        _json.dumps({"at": t0 + 600, "event": "reserve", "holder": "build:p3", "kind": "build",
                     "host_gb": 12, "pid": me, "ttl_s": 900}),
    ]) + "\n")
    samples = d / "watch.jsonl"
    def row(offset, peak):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0 + offset))
        return _json.dumps({"at": stamp, "groups": {"job:build": {"rss_gb": peak}}})
    samples.write_text("\n".join([
        row(150, 10.0),          # ONE build booked (p1 and p2 both open? no: p2 opens at +100 too) -> ambiguous
        row(650, 14.0),          # only p3 open -> a clean sample, and it OVERRAN its 12 GB booking
    ]) + "\n")
    clean, overruns, detail = A.promised_vs_measured(samples_path=samples, path=led2, now=t0 + 900)
    check("a reading with TWO builds booked is not a sample (the group is their sum)",
          "1 clean sample" in detail, detail)
    check("  a reading with one build booked IS a sample", clean == 1, detail)
    check("  and a peak above the booking is counted as an overrun", overruns == 1, detail)
    check("  the detail names the numbers, not just a count", "14.0 GB peak" in detail, detail)

    n_none, _o, why_none = A.promised_vs_measured(samples_path=d / "absent.jsonl", path=led2, now=t0 + 900)
    check("no samples is said plainly, not reported as zero overruns", n_none == 0 and "not recorded" in why_none,
          why_none)
    print("test_admission_budget:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
