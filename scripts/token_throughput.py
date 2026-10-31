#!/usr/bin/env python3
"""TRUE tokens per second: the whole model, and whether more lanes is more throughput.

    python3 scripts/token_throughput.py --observe 30        # no load: the model's own counters, 30 s
    python3 scripts/token_throughput.py --lanes 1,2,4       # controlled: k concurrent requests each
    python3 scripts/token_throughput.py --observe 20 --lanes 1,2,4

Jas, 2026-10-29: "I want a way to calculate the true tokens per second. If each lane slows, but I get more
lanes is there more throughput? Or is the tokens per second the total model's? Can you determine that?"

TWO DIFFERENT NUMBERS, and confusing them is how a box gets tuned the wrong way:

  * MODEL THROUGHPUT is the whole engine's: `vllm:generation_tokens_total` is a counter, so its delta over a
    window divided by the window IS the model's true tok/s -- everything, including work this tool did not
    cause. That number is what the box can actually do, and it does not care how many "lanes" you believe in.
  * PER-REQUEST THROUGHPUT is what one caller sees. Under batching it FALLS as concurrency rises: the engine
    is already filling its compute, so an extra request shares the same throughput rather than adding to it.

So the question "more lanes, each slower: more total?" is answered by measuring BOTH at each concurrency, and
the shape to look for is: aggregate rising while per-request falls means the engine had spare batch room;
aggregate FLAT while per-request falls means it did not, and adding lanes only splits the same pie.

MEASURED, NOT MODELLED. The counters come from the server; the per-request numbers come from each reply's own
`usage.completion_tokens` over its own wall time. Nothing here is estimated from characters.
"""
import argparse
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
DEFAULT_BASE = "http://172.18.0.2:8000"


def _get(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def counters(base=DEFAULT_BASE):
    """(generation_tokens, prompt_tokens, requests_running) from the engine's own metrics, or None."""
    try:
        text = _get(f"{base}/metrics")
    except OSError:
        return None
    gen = prom = running = None
    for line in text.splitlines():
        if line.startswith("vllm:generation_tokens_total"):
            gen = float(line.rsplit(" ", 1)[1])
        elif line.startswith("vllm:prompt_tokens_total"):
            prom = float(line.rsplit(" ", 1)[1])
        elif line.startswith("vllm:num_requests_running"):
            running = float(line.rsplit(" ", 1)[1])
    if gen is None:
        return None
    return gen, prom, running


def observe(seconds, base=DEFAULT_BASE):
    """The model's true throughput over `seconds`, with no load added by this tool."""
    a = counters(base)
    if a is None:
        return {"error": f"no metrics at {base}/metrics"}
    t0 = time.time()
    time.sleep(max(1.0, float(seconds)))
    b = counters(base)
    dt = time.time() - t0
    if b is None:
        return {"error": "metrics became unreadable during the window"}
    g = (b[0] - a[0]) / dt
    p = ((b[1] or 0) - (a[1] or 0)) / dt
    return {"window_s": round(dt, 1), "generation_tok_s": round(g, 2), "prompt_tok_s": round(p, 2),
            "requests_running_at_end": b[2],
            "tokens_generated_in_window": int(b[0] - a[0])}


def one_request(base, prompt, max_tokens):
    """(completion_tokens, seconds) for a single chat completion."""
    body = json.dumps({"model": "swift", "max_tokens": max_tokens, "temperature": 0,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(f"{base}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except OSError as e:
        return None, time.time() - t0, f"{type(e).__name__}: {e}"
    dt = time.time() - t0
    usage = data.get("usage") or {}
    return int(usage.get("completion_tokens") or 0), dt, ""


def lanes_experiment(counts, max_tokens, prompt, base=DEFAULT_BASE, repeat=2):
    """One row per concurrency: per-request tok/s, AGGREGATE = tokens/wall, and the wall time.

    AGGREGATE IS ONE DIVISION, not the sum of the per-request rates (2026-10-29, first version). Summing rates
    double-counts the overlap and made a noisy run read as a 16x win: the same concurrency produced 28 tok/s in
    one run and 37 in another purely from a cold first request. Each level is now warmed once (discarded) and
    repeated, and the MEDIAN is reported, so what is published is a shape rather than one lucky batch.
    """
    rows = []
    for k in counts:
        one_request(base, "warm up", 8)          # discard: the first request pays for the engine's cold path
        trials = []
        for _ in range(max(1, int(repeat))):
            results, threads = [], []

            def work():
                results.append(one_request(base, prompt, max_tokens))

            t0 = time.time()
            for _ in range(int(k)):
                t = threading.Thread(target=work)
                t.start()
                threads.append(t)
            for t in threads:
                t.join()
            wall = time.time() - t0
            ok = [(n, dt) for n, dt, err in results if n]
            errs = [e for _n, _dt, e in results if e]
            per = [n / dt for n, dt in ok if dt > 0]
            tokens = sum(n for n, _dt in ok)
            trials.append({"ok": len(ok), "errors": errs[:2],
                           "per_request_tok_s": (sum(per) / len(per)) if per else None,
                           "aggregate_tok_s": (tokens / wall) if wall > 0 and tokens else None,
                           "wall_s": wall, "tokens": tokens})
        good = [t for t in trials if t["aggregate_tok_s"]]
        pick = lambda key: (sorted(t[key] for t in good)[len(good) // 2] if good else None)   # noqa: E731
        rows.append({"lanes": int(k), "ok": pick("ok"), "trials": len(trials),
                     "errors": (good or trials)[-1]["errors"],
                     "per_request_tok_s": round(pick("per_request_tok_s"), 2) if pick("per_request_tok_s") else None,
                     "aggregate_tok_s": round(pick("aggregate_tok_s"), 2) if pick("aggregate_tok_s") else None,
                     "wall_s": round(pick("wall_s"), 1) if pick("wall_s") else None,
                     "tokens": pick("tokens")})
    return rows


def verdict(rows):
    """What the shape says: did extra concurrency buy throughput, or split it?"""
    good = [r for r in rows if r.get("aggregate_tok_s")]
    if len(good) < 2:
        return "not enough lanes completed to compare"
    first, last = good[0], good[-1]
    agg = (last["aggregate_tok_s"] or 0) / max(1e-9, first["aggregate_tok_s"] or 1)
    per = (last["per_request_tok_s"] or 0) / max(1e-9, first["per_request_tok_s"] or 1)
    if agg >= 1.25:
        return (f"MORE LANES BOUGHT THROUGHPUT: aggregate {first['aggregate_tok_s']} -> "
                f"{last['aggregate_tok_s']} tok/s ({agg:.2f}x) while each request fell to {per:.2f}x. "
                f"The engine had spare batch room.")
    if agg >= 0.9:
        return (f"THE PIE SPLIT, IT DID NOT GROW: aggregate {first['aggregate_tok_s']} -> "
                f"{last['aggregate_tok_s']} tok/s ({agg:.2f}x) while each request fell to {per:.2f}x. "
                f"Adding lanes divides the same throughput; the model's total is the wall.")
    return (f"MORE LANES MADE IT WORSE: aggregate {first['aggregate_tok_s']} -> {last['aggregate_tok_s']} "
            f"tok/s ({agg:.2f}x) and each request {per:.2f}x. Beyond this point lanes cost throughput.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--observe", type=float, default=0.0, help="seconds to watch the model's own counters")
    ap.add_argument("--lanes", default="", help="comma-separated concurrency counts, e.g. 1,2,4")
    ap.add_argument("--max-tokens", type=int, default=32)
    ap.add_argument("--repeat", type=int, default=2, help="trials per concurrency (median reported)")
    ap.add_argument("--prompt", default="Reply with the numbers one to twenty in order, nothing else.")
    a = ap.parse_args(argv)

    out = {}
    if a.observe:
        print(f"  observing the model's own counters for {a.observe:g}s (adds no load)...")
        out["model"] = observe(a.observe, a.base)
        m = out["model"]
        if "error" in m:
            print(f"  MODEL: {m['error']}")
        else:
            print(f"  MODEL THROUGHPUT: {m['generation_tok_s']} generation tok/s over {m['window_s']}s "
                  f"({m['tokens_generated_in_window']} tokens actually generated by the whole engine), "
                  f"prompt {m['prompt_tok_s']} tok/s")
    if a.lanes:
        counts = [int(x) for x in str(a.lanes).replace(" ", "").split(",") if x]
        print(f"  controlled: {counts} lane(s), max_tokens {a.max_tokens} "
              f"(this DOES add load: {sum(counts)} requests in total)")
        out["lanes"] = lanes_experiment(counts, a.max_tokens, a.prompt, a.base, repeat=a.repeat)
        print(f"\n  {'lanes':>5} {'per-request tok/s':>18} {'aggregate tok/s':>16} {'wall s':>7} {'tokens':>7}")
        for r in out["lanes"]:
            print(f"  {r['lanes']:>5} {str(r['per_request_tok_s']):>18} {str(r['aggregate_tok_s']):>16} "
                  f"{r['wall_s']:>7} {r['tokens']:>7}"
                  + (f"   ERRORS {r['errors']}" if r.get("errors") else ""))
        print(f"\n  {verdict(out['lanes'])}\n")
    if not a.observe and not a.lanes:
        print("  nothing asked for: use --observe N and/or --lanes 1,2,4")
        return 2
    # LEAVE THE CURVE BEHIND (2026-10-29): scripts/capacity_model.py decides how many lanes to run from a
    # MEASURED curve, and a curve that only existed in this process's output would never reach it. One point per
    # lane count, the best aggregate seen for that count.
    if out.get("lanes"):
        try:
            curve = {str(r["lanes"]): r["aggregate_tok_s"] for r in out["lanes"] if r.get("aggregate_tok_s")}
            if curve:
                path = Path.home() / ".cache" / "hivemind-tmp" / "throughput-curve.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                old_pts = {}
                try:
                    old_pts = json.loads(path.read_text(encoding="utf-8")).get("curve") or {}
                except (OSError, ValueError):
                    old_pts = {}
                old_pts.update(curve)
                path.write_text(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                            "curve": old_pts}, indent=1), encoding="utf-8")
                print(f"  curve written to {path} ({len(old_pts)} lane count(s) measured)")
        except OSError as e:
            print(f"  NOTE: the curve was not written ({type(e).__name__}: {e}) -- the run itself stands",
                  file=sys.stderr)
    print(json.dumps(out, indent=1)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
