#!/usr/bin/env python3
"""One peak per BUILD, not one per 20-second sample (A5, 2026-10-30).

The brief: "A5 the per-process sampler -- what a build actually spends its RAM on is UNPROVEN; the 8.06 GB figure
rests on it, and per-build peaks are needed for A3."

SETTLED BY THE DATA, not by the docstring that claimed otherwise: `build_peaks()` returned 56,826 values from
workspace/memory-watch.jsonl, and 1,051 production builds have ever started. Each value was the `job:build` group's
total at one instant -- the SUM of whatever builds were running -- so the "p95 of recorded peaks" was a p95 of
concurrent usage, not of any build's peak. The samples carry no build identity at all.

So the collector now records `build_procs` (pid -> GB) and this reads it, joining pid to the chunk in the dispatch
ledger's `started` rows and keeping one maximum per build.

RED ON MAIN: capacity_model has no per_build_peaks (AttributeError).
"""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import capacity_model as cm          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


def write(path, rows):
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


with tempfile.TemporaryDirectory() as td:
    samples = Path(td) / "samples.jsonl"
    runs = Path(td) / "runs.jsonl"
    write(runs, [
        {"event": "started", "at": "2026-10-30T01:00:00Z", "pid": 100, "chunk": "p1-a"},
        {"event": "started", "at": "2026-10-30T02:00:00Z", "pid": 200, "chunk": "p2-b"},
        {"event": "started", "at": "2026-10-30T05:00:00Z", "pid": 100, "chunk": "p3-c"},   # pid reused
    ])
    write(samples, [
        {"at": "2026-10-30T01:00:20Z", "build_procs": {"100": 3.0}},   # 200 has not started yet
        {"at": "2026-10-30T01:00:40Z", "build_procs": {"100": 5.0}},          # p1-a's peak
        {"at": "2026-10-30T02:00:20Z", "build_procs": {"200": 2.0}},          # p2-b's peak
        {"at": "2026-10-30T05:00:20Z", "build_procs": {"100": 9.0}},          # p3-c, after the pid was reused
        {"at": "2026-10-30T06:00:00Z", "build_procs": {"999": 4.0}},          # a pid no started row names
        {"at": "2026-10-30T00:00:00Z", "groups": {"job:build": {"rss_gb": 7.0}}},   # an OLD row: no pids
    ])
    peaks, report = cm.per_build_peaks(samples, runs)
    got = dict(peaks)
    check("one peak per build, keyed by its chunk", got == {"p1-a": 5.0, "p2-b": 2.0, "p3-c": 9.0}, str(got))
    check("a build's peak is its maximum sample, not its last", got.get("p1-a") == 5.0, str(got.get("p1-a")))
    check("pid reuse attributes to the build that was running", got.get("p3-c") == 9.0 and
          got.get("p1-a") == 5.0, str(got))
    check("a pid no started row names is REPORTED, not dropped", report.get("unattributed_pids") == ["999"],
          str(report.get("unattributed_pids")))
    check("rows from before the sampler existed are counted, not silently ignored",
          report.get("samples_without_pids") == 1, str(report.get("samples_without_pids")))
    check("the report says how many builds it measured", report.get("builds") == 3, str(report.get("builds")))
    check("nothing at all is an empty list, never a guess", cm.per_build_peaks(Path(td) / "nope.jsonl", runs) ==
          ([], {"builds": 0, "samples_without_pids": 0, "unattributed_pids": []}),
          str(cm.per_build_peaks(Path(td) / "nope.jsonl", runs)))

# --- and the myth in the docstring is corrected -----------------------------------------------
doc = (HERE / "scripts" / "capacity_model.py").read_text(encoding="utf-8")
check("build_peaks no longer calls its samples 'the peak host memory of every build'",
      "The peak host memory of every build" not in doc, "the first line of its docstring")
check("  and it points at per_build_peaks for the real thing", "per_build_peaks" in doc)

print("test_per_build_peaks:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
