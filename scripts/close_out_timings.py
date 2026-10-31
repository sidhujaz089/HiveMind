#!/usr/bin/env python3
"""Where a close-out's time went, so the gate's timeout can be DERIVED instead of guessed (t9580, 2026-10-31).

Jas: *"900 is too short and if we don't have data, make the timeout 30 minutes and see if that captures most things. I
want session ends to generate data that we can see on a graph. If we see outliers that is the point we target our CI."*

The 1800 s in `scripts/hooks/pre-commit` is an interim guess, and it is only better than the 900 it replaced because it
says it is one. This is the other half: one row per close-out in `workspace/close-out-timings.jsonl`, with the whole
run, the invariant check, and every section, so the next number comes from measurements rather than from another
round number.

    python3 scripts/close_out_timings.py            # p50 / p95 / max, and the outliers by name
    python3 scripts/close_out_timings.py --graph    # an ASCII trend of the whole run, for a terminal
    python3 scripts/close_out_timings.py --json     # the rows, for a page to plot

An outlier is a run more than `factor` times the median for that measure. That is deliberately crude and deliberately
visible: the point is to point at one, not to model the distribution.
"""
import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LEDGER = REPO / "workspace" / "close-out-timings.jsonl"


def load(path=None):
    """The recorded rows, oldest first. A missing ledger is an empty list, not an error."""
    p = Path(path) if path else LEDGER
    try:
        return parse(p.read_text(encoding="utf-8"))
    except OSError as e:                                           # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: no close-out timings at {p} ({type(e).__name__}) -- nothing recorded yet", file=sys.stderr)
        return []


def parse(text):
    """Rows from a JSONL blob, skipping anything unreadable rather than losing the whole ledger to one bad line."""
    out = []
    for i, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as e:
            print(f"  NOTE: line {i} is not JSON ({e}) -- skipped", file=sys.stderr)
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def record(row, path=None):
    """Append one close-out's timings. Never raises: a close-out must not fail because telemetry could not be written."""
    p = Path(path) if path else LEDGER
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        return True
    except OSError as e:                                           # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: could not record close-out timings ({type(e).__name__}: {e})", file=sys.stderr)
        return False


def _stat(values):
    vals = sorted(float(v) for v in values if isinstance(v, (int, float)))
    if not vals:
        return {"n": 0, "p50": 0.0, "p95": 0.0, "max": 0.0, "min": 0.0}
    def at(q):
        return vals[min(len(vals) - 1, int(q * (len(vals) - 1)))]
    return {"n": len(vals), "p50": round(at(0.50), 1), "p95": round(at(0.95), 1),
            "max": round(vals[-1], 1), "min": round(vals[0], 1)}


def summarise(rows):
    """{measure: stat} for the whole run, the invariant check, and every section seen in the rows."""
    measures = {"total_s": [r.get("total_s") for r in rows],
                "check_s": [r.get("check_s") for r in rows]}
    names = set()
    for r in rows:
        names.update((r.get("sections") or {}))
    for name in sorted(names):
        measures[name] = [(r.get("sections") or {}).get(name) for r in rows]
    return {k: _stat(v) for k, v in measures.items() if _stat(v)["n"]}


def outliers(stats, factor=2.0):
    """{measure: stat} where the worst run is more than `factor` times the median, with at least 3 runs to compare."""
    out = {}
    for name, s in (stats or {}).items():
        if s["n"] >= 3 and s["p50"] > 0 and s["max"] > factor * s["p50"]:
            out[name] = s
    return out


def graph(rows, width=48):
    """(lines) an ASCII trend of the whole run, newest last. A terminal does not need a plotting library."""
    vals = [float(r.get("total_s") or 0) for r in rows]
    if not vals:
        return ["  (no runs recorded)"]
    hi = max(vals) or 1.0
    lines = []
    for r, v in list(zip(rows, vals))[-24:]:
        bar = "#" * max(0, int(width * v / hi))
        lines.append(f"  {(r.get('at') or '?')[:16]:17s} {v:7.0f}s {bar}")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description="Where close-outs spend their time.")
    ap.add_argument("--graph", action="store_true", help="an ASCII trend of the whole run")
    ap.add_argument("--json", action="store_true", help="the rows themselves")
    ap.add_argument("--factor", type=float, default=2.0, help="an outlier is this many times the median (default 2)")
    a = ap.parse_args(argv)
    rows = load()
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    if a.graph:
        print(f"  close-out wall clock, last 24 of {len(rows)} run(s):")
        for line in graph(rows):
            print(line)
        return 0
    if not rows:
        print("  no close-out timings recorded yet")
        return 0
    stats = summarise(rows)
    print(f"  {len(rows)} close-out run(s), p50 / p95 / max seconds:")
    for name, s in stats.items():
        print(f"    {name[:44]:46s} {s['p50']:8.1f} {s['p95']:8.1f} {s['max']:8.1f}   (n={s['n']})")
    out = outliers(stats, a.factor)
    if out:
        print(f"\n  OUTLIERS (worst run > {a.factor:g}x the median) -- Jas: this is where CI gets pointed:")
        for name, s in out.items():
            print(f"    {name[:44]:46s} median {s['p50']:.0f}s, worst {s['max']:.0f}s "
                  f"({s['max'] / s['p50']:.1f}x)")
    else:
        print(f"\n  no outlier: nothing exceeds {a.factor:g}x its median yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
