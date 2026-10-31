#!/usr/bin/env python3
"""One row per model call: tokens, seconds, model, harness -- so later tuning has evidence.

    from run_telemetry import record
    record(harness="coder.py", model=m, num_predict=16000, eval_count=n,
           seconds=s, done_reason=d, timeout_s=t, ok=True)

    python3 scripts/run_telemetry.py              # what the rows say
    python3 scripts/run_telemetry.py --json

WHY, 2026-09-14. Jas: *"If we can also collect data on tokens and time, and the model and
harness for each run, then we can have evidence for later optimizations."* And the immediate
use: TIMEOUT_SAFETY_FACTOR was set to 2.0 that day by judgement, not measurement -- his words,
*"We can fine tune later with data."* These are that data.

## THE ONE NUMBER TO LOOK AT

`observed_seconds / timeout_s`. If that ratio is reliably far below 0.5 the safety factor is
too generous and a wedged server takes longer to notice than it needs to. If rows cluster near
1.0, the factor is too tight and real work is being killed. Neither can be known without rows,
which is why this exists before the tuning rather than after.

## WHAT IT DELIBERATELY DOES NOT DO

* IT DOES NOT TRUNCATE. No field is sliced. This repository removed every fixed-width slice
  from every record on the day this file was written; see docs/skills/never-truncate-a-record.
* IT NEVER RAISES INTO THE CALLER. A telemetry failure must not break a dispatch -- but it is
  not silent either: it prints to stderr, because `except: pass` is the handler that hid a
  dead Gemma supervisor for 1385 heartbeats.
* IT DOES NOT COMPUTE tok/s FOR A COLD LOAD without saying so. load_seconds is recorded
  separately, because 11.1 tok/s cold and 35.7 warm on the SAME tag is the single most
  misleading pair in this repository's history -- one cold figure was quoted as fact for a day.

## WHY A SEPARATE LEDGER FROM measurements.jsonl

`workspace/measurements.jsonl` holds DELIBERATE measurements: a number someone set out to
establish, with its conditions, gated by check_measurements.py (L-147). This is the opposite --
incidental observation of ordinary work, high volume, unsupervised. Mixing them would let
routine noise dilute a figure that was carefully established, and would make L-147's gate
meaningless. They answer different questions and stay apart.
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "workspace" / "run-telemetry.jsonl"


def record(harness, model, seconds, num_predict=None, eval_count=None, prompt_eval_count=None,
           done_reason=None, timeout_s=None, load_seconds=None, ok=None, note=None, **extra):
    """Append one row. Returns the row, or None if it could not be written.

    Every argument is optional except the four Jas asked for -- harness, model, tokens, time --
    because a row missing a field is worth more than no row, and a caller that has to assemble
    a complete record before it can log anything logs nothing.
    """
    row = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "harness": harness,
        "model": model,
        "seconds": round(float(seconds), 2) if seconds is not None else None,
        "num_predict": num_predict,
        "eval_count": eval_count,
        "prompt_eval_count": prompt_eval_count,
        "done_reason": done_reason,
        "timeout_s": timeout_s,
        "load_seconds": load_seconds,
        "ok": ok,
        "note": note,
    }
    # tok/s ONLY when it means something: tokens generated, time spent, and the load time
    # separated out so a cold run does not masquerade as a decode rate.
    try:
        if eval_count and seconds and float(seconds) > 0:
            decode = float(seconds) - float(load_seconds or 0)
            if decode > 0:
                row["tok_s_decode"] = round(float(eval_count) / decode, 2)
                row["cold"] = bool(load_seconds and float(load_seconds) > 5)
    except (TypeError, ValueError) as e:
        print(f"  NOTE: decode rate not computed ({e})", file=sys.stderr)
    # HOW CLOSE THIS RUN CAME TO ITS TIMEOUT -- the number the safety factor is tuned on.
    try:
        if timeout_s and seconds:
            row["used_fraction_of_timeout"] = round(float(seconds) / float(timeout_s), 3)
    except (TypeError, ValueError, ZeroDivisionError) as e:
        print(f"  NOTE: timeout fraction not computed ({e})", file=sys.stderr)
    row.update(extra)
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return row
    except OSError as e:
        # NOT SILENT. See the module docstring.
        print(f"run_telemetry: could not write a row: {e}", file=sys.stderr)
        return None



def observed_seconds_per_token(harness, model, window=100, quantile=0.90):
    """(seconds-per-token, rows_used) for this harness+model, from recent SUCCESSFUL runs.

    This is what settings.timeout_for() tunes on. Four rules, each one a way it would otherwise
    be wrong:

    * PER TOKEN, not per run. Runs have different budgets; raw seconds cannot be compared.
    * EXCLUDE KILLED RUNS (ok is False). A run that hit its timeout recorded seconds == the
      timeout, and feeding that back teaches the estimator that the cap is normal -- each
      round tightening until everything is killed.
    * EXCLUDE COLD LOADS from the rate but not from reality: load_duration is subtracted so the
      rate describes DECODE, and the caller adds its own prompt/load slack. 11.1 tok/s cold
      against 35.7 warm on the same tag is the pair that makes this necessary.
    * A HIGH QUANTILE, not the mean. The doubling upstream should be headroom on a bad case.

    Reads only the TAIL of the ledger, so this stays cheap enough to call on every dispatch.
    """
    if not LEDGER.is_file():
        return None, 0
    try:
        # Tail without loading a large file: read the last chunk and keep whole lines.
        size = LEDGER.stat().st_size
        want = min(size, 400_000)
        with LEDGER.open("rb") as f:
            f.seek(size - want)
            blob = f.read().decode("utf-8", errors="replace")
        lines = blob.splitlines()
        if want < size and lines:
            lines = lines[1:]                 # the first line is probably a fragment
    except OSError as e:
        print(f"run_telemetry: could not read the ledger: {e}", file=sys.stderr)
        return None, 0

    rates = []
    for line in reversed(lines):
        if len(rates) >= window:
            break
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            print(f"  NOTE: unreadable telemetry row -- not counted", file=sys.stderr)
            continue
        if r.get("harness") != harness or r.get("model") != model:
            continue
        if r.get("ok") is False:
            continue
        secs, toks = r.get("seconds"), r.get("eval_count")
        if not isinstance(secs, (int, float)) or not isinstance(toks, (int, float)) or toks <= 0:
            continue
        decode = float(secs) - float(r.get("load_seconds") or 0)
        if decode <= 0:
            continue
        rates.append(decode / float(toks))
    if not rates:
        return None, 0
    rates.sort()
    # index of the quantile, clamped so a tiny sample uses its slowest row rather than its mean
    i = min(len(rates) - 1, max(0, int(round(quantile * (len(rates) - 1)))))
    return rates[i], len(rates)

def rows():
    if not LEDGER.is_file():
        return [], 0
    out, torn = [], 0
    for line in LEDGER.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            torn += 1
    return out, torn


def summarise():
    rs, torn = rows()
    if not rs:
        # ABSENT IS NOT CLEAN (L-146): say nothing was looked at.
        return {"rows": 0, "torn": torn,
                "say": f"no rows yet in {LEDGER.relative_to(REPO)} -- nothing has been recorded, "
                       f"which is not the same as everything being fast."}
    by = {}
    for r in rs:
        key = (r.get("harness"), r.get("model"))
        by.setdefault(key, []).append(r)
    out = {"rows": len(rs), "torn": torn, "ledger": str(LEDGER.relative_to(REPO)), "groups": []}
    for (h, m), g in sorted(by.items(), key=lambda kv: -len(kv[1])):
        secs = [x["seconds"] for x in g if isinstance(x.get("seconds"), (int, float))]
        rate = [x["tok_s_decode"] for x in g
                if isinstance(x.get("tok_s_decode"), (int, float)) and not x.get("cold")]
        frac = [x["used_fraction_of_timeout"] for x in g
                if isinstance(x.get("used_fraction_of_timeout"), (int, float))]
        out["groups"].append({
            "harness": h, "model": m, "n": len(g),
            "seconds_median": round(statistics.median(secs), 1) if secs else None,
            "seconds_max": round(max(secs), 1) if secs else None,
            "tok_s_warm_median": round(statistics.median(rate), 2) if rate else None,
            "timeout_used_median": round(statistics.median(frac), 3) if frac else None,
            "timeout_used_max": round(max(frac), 3) if frac else None,
            "truncated": sum(1 for x in g if x.get("done_reason") == "length"),
            "failed": sum(1 for x in g if x.get("ok") is False),
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="What the run telemetry says.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    s = summarise()
    if a.json:
        print(json.dumps(s, indent=2))
        return 0
    if not s["rows"]:
        print("  " + s["say"])
        return 0
    print(f"\n  {s['rows']} row(s) in {s['ledger']}"
          + (f", {s['torn']} unreadable" if s["torn"] else ""))
    print(f"\n  {'harness':22} {'model':26} {'n':>4} {'med s':>7} {'max s':>7} "
          f"{'tok/s':>6} {'timeout used':>13} {'trunc':>6}")
    for g in s["groups"]:
        print(f"  {str(g['harness'])[:22]:22} {str(g['model'])[:26]:26} {g['n']:>4} "
              f"{str(g['seconds_median']):>7} {str(g['seconds_max']):>7} "
              f"{str(g['tok_s_warm_median']):>6} "
              f"{str(g['timeout_used_median']) + ' / ' + str(g['timeout_used_max']):>13} "
              f"{g['truncated']:>6}")
    print("\n  'timeout used' is median / max of seconds divided by the timeout allowed.")
    print("  Reliably far below 0.5 means TIMEOUT_SAFETY_FACTOR (settings.py) is too generous")
    print("  and a wedged server takes longer to notice than it needs to. Near 1.0 means it is")
    print("  too tight and real work is being killed. That is the number to tune on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
