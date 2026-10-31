#!/usr/bin/env python3
"""lane_status shows manager runs, names a default model by role, and reports a RECENT rate.

2026-09-23: the Q4 manager queue (ask_q16.py) was invisible to lane_status, a preflight lane
showed model "(default)", and the only rate was an all-time median that cannot see contention.
Against fixtures, never the live box.
"""
import io
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "local-inference")]
import lane_status as LS  # noqa: E402
import roles  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def row(model, tok, ago, now):
    return json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - ago)),
                       "model": model, "tok_s_decode": tok, "eval_count": 500})


def main():
    report("ask_q16.py is a lane, of kind manager", LS.LANE_SCRIPTS.get("ask_q16.py"), "manager")
    argv = ["python3", "scripts/preflight_brief.py", "--brief", "x.txt"]
    report("a preflight with no --model is named by the manager role",
           LS.model_of(999999, argv, {}), roles.tag("manager", "(default)"))
    report("an explicit --model still wins",
           LS.model_of(999999, argv + ["--model", "gemma4:26b"], {}), "gemma4:26b")

    now = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        LS.TELEMETRY = Path(tmp) / "t.jsonl"
        LS.TELEMETRY.write_text("\n".join([row("q4", 18.0, 60, now), row("q4", 19.0, 600, now),
                                           row("q4", 6.0, 7200, now), row("g", 50.0, 3000, now)]))
        got = LS.rates_recent(now=now)
    report("recent rate uses only calls from the last 30 min", round(got["q4"][0], 1), 18.5)
    report("  and counts them", got["q4"][1], 2)
    report("  and reports the newest one's age (timestamps are whole seconds)", 59 <= got["q4"][2] <= 61, True)
    report("a model with no recent call has no recent rate", "g" in got, False)

    import console
    with tempfile.TemporaryDirectory() as tmp:
        console.TELEMETRY = Path(tmp) / "t.jsonl"
        console._telemetry("q4", {"eval_count": 900, "eval_duration": 50e9, "done_reason": "stop"},
                           52.0, 4000)
        r = json.loads(console.TELEMETRY.read_text())
    report("a console call writes a telemetry row with its decode rate",
           (r["harness"], r["model"], r["tok_s_decode"]), ("console", "q4", 18.0))
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
