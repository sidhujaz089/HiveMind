#!/usr/bin/env python3
"""t1325: a red-on-main verdict must leave the failure output on disk.

RCA 30c4cda07083: dispatch_loop's red_of callback runs `python3 scripts/tests/<suite>.py`
against a sandbox copy of main, reports the suite RED, and throws the run away -- no exit
code, no stderr, no duration. Twelve "Red on main" tasks (t1193-t1204) were filed and closed;
the assistant ran the suite standalone, got 28/28, said "probably a flake under load", and two
days later nothing on disk says what killed the suite (workspace/red-on-main.jsonl has no row
for it, workspace/suite-state.jsonl no red row for it). The next flake is unprovable the same
way, and the next such task again prints "Turned red at: unknown".

This suite is RED today because scripts/dispatch_loop.py has no record_red_verdict: every case
below raises AttributeError. Fakes and temp dirs only -- nothing here writes
workspace/red-on-main.jsonl, the ceiling, the queue, a model, a server, or a process left
running.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # scripts/tests/ -> repo root
sys.path[:0] = [str(ROOT / "scripts")]
import dispatch_loop as D                            # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"  (got {got!r}, want {want!r})"))


def fake_runner(spec):
    """Stand-in for the suite run. Returns exactly the fields record_red_verdict keeps."""
    def run(argv, seconds):
        row = {"argv": list(argv), "seconds": seconds, "exit_code": spec["exit_code"],
               "stdout": spec.get("stdout", ""), "stderr": spec.get("stderr", ""),
               "outcome": spec.get("outcome", "ran")}
        if "error" in spec:
            row["error"] = spec["error"]
        return row
    return run


def fake_clock(start, stop):
    """A clock with two DISTINCT readings, so duration_s is measured, never echoed back."""
    readings = [start, stop]

    def now():
        return readings.pop(0) if readings else stop
    return now


# ---- 1. a RED verdict appends exactly one row, carrying the failure output whole
rows = []
row = D.record_red_verdict("test_task_routing.py", "p487-t1090-flow-coverage",
                           runner=fake_runner({"exit_code": 1,
                                               "stderr": "AssertionError: boom\n",
                                               "stdout": "28 passed, 0 failed\n"}),
                           append=rows.append, seconds_fn=fake_clock(100.0, 103.5))
report("1 a red verdict returns a row dict", isinstance(row, dict), True)
report("  exactly one row reached the ledger", len(rows), 1)
r = rows[0] if rows else {}
report("  event is red-verdict", r.get("event"), "red-verdict")
report("  the suite is named by name, not a placeholder", r.get("suite"), "test_task_routing.py")
report("  the refusal that named it is carried", r.get("refused_chunk"), "p487-t1090-flow-coverage")
report("  the exit code is recorded", r.get("exit_code"), 1)
report("  the stderr is recorded WHOLE", r.get("stderr"), "AssertionError: boom\n")
report("  the stdout is recorded WHOLE", r.get("stdout"), "28 passed, 0 failed\n")
report("  the duration is MEASURED from the clock (103.5 - 100.0)",
       round(float(r.get("duration_s", -1)), 2), 3.5)
report("  the run's argv is recorded (interpreter first)",
       (r.get("argv") or [None])[0], sys.executable)
report("  argv points at the suite under scripts/tests",
       Path(str((r.get("argv") or [None, None, None])[-1])).parts[-3:] if len(r.get("argv") or []) > 1 else None,
       ("scripts", "tests", "test_task_routing.py"))
report("  the pid that ran it is recorded", isinstance(r.get("runner_pid"), int), True)
report("  a timestamp is recorded", bool(str(r.get("at", ""))), True)
report("  the caller is told it ran", r.get("outcome"), "ran")
report("  the row survives a json round-trip", json.loads(json.dumps(r)), r)

# ---- 2. a GREEN verdict records NOTHING: the ledger drives task filing, junk rows flake it
rows2 = []
g = D.record_red_verdict("test_a.py", "p1-x",
                         runner=fake_runner({"exit_code": 0, "stdout": "all good\n"}),
                         append=rows2.append, seconds_fn=fake_clock(1.0, 1.4))
report("2 a green verdict writes no row", rows2, [])
report("  and says it ran, not that it failed", g.get("outcome"), "ran")
report("  keeping the green exit code for the caller", g.get("exit_code"), 0)

# ---- 2b. the runner is handed the recorded ceiling, not an ad-hoc number
seen = {}


def spy(argv, seconds):
    seen["argv"] = list(argv)
    seen["seconds"] = seconds
    return {"argv": list(argv), "seconds": seconds, "exit_code": 1,
            "stdout": "", "stderr": "x\n", "outcome": "ran"}


D.record_red_verdict("test_task_routing.py", "p487-t1090-flow-coverage", runner=spy,
                      append=lambda row: None, seconds_fn=fake_clock(0.0, 0.5))
report("2b the runner is handed the suite-run ceiling", seen.get("seconds"),
       float(getattr(D, "SUITE_RUN_TIMEOUT_S", 0)))
report("  the suite name reaches the runner intact",
       Path(str((seen.get("argv") or [None, ""])[-1])).name, "test_task_routing.py")

# ---- 3. a TIMEOUT is recorded with its partial output -- the mechanism the RCA hypothesised
rows3 = []
r3 = D.record_red_verdict("test_b.py", "p2-y",
                          runner=fake_runner({"exit_code": "timeout", "outcome": "timeout",
                                              "stderr": "partial: 12 of 28 assertions\n"}),
                          append=rows3.append, seconds_fn=fake_clock(100.0, 700.0))
report("3 a timeout is recorded, not swallowed", len(rows3), 1)
r3row = rows3[0] if rows3 else {}
report("  with exit_code naming the cause", r3row.get("exit_code"), "timeout")
report("  with the partial stderr the run reached",
       r3row.get("stderr"), "partial: 12 of 28 assertions\n")
report("  and the full time it took", round(float(r3row.get("duration_s", -1)), 2), 600.0)
report("  the caller is told it timed out", r3.get("outcome"), "timeout")

# ---- 4. a suite that cannot be run records WHY (a bare `pass` is the other half of this bug)
rows4 = []
r4 = D.record_red_verdict("test_c.py", "p3-z",
                          runner=fake_runner({"exit_code": "not-found", "outcome": "not-found",
                                              "error": "FileNotFoundError: no such file"}),
                          append=rows4.append, seconds_fn=fake_clock(1.0, 1.2))
report("4 an unrunnable suite is recorded", len(rows4), 1)
r4row = rows4[0] if rows4 else {}
report("  with the reason in the row", r4row.get("error"), "FileNotFoundError: no such file")
report("  and reported back to the caller", r4.get("outcome"), "not-found")

# ---- 5. the DEFAULT path runs a real suite, in a temp dir, capturing whole output
with tempfile.TemporaryDirectory(prefix="t1325-") as td:
    repo = Path(td)
    probe = repo / "scripts" / "tests" / "zzz_t1325_probe.py"
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text("import sys\n"
                     "sys.stderr.write('boom-3f9c2a\\n')\n"
                     "sys.stdout.write('stdout-7be14d\\n')\n"
                     "sys.exit(4)\n", encoding="utf-8")
    rows5 = []
    r5 = D.record_red_verdict("zzz_t1325_probe.py", "p5-probe", repo=repo, append=rows5.append)
    report("5 the default runner runs the suite for real", len(rows5), 1)
    r5row = rows5[0] if rows5 else {}
    report("  and records its real exit code", r5row.get("exit_code"), 4)
    report("  capturing the WHOLE stderr (not a tail)",
           "boom-3f9c2a" in str(r5row.get("stderr", "")), True)
    report("  capturing the WHOLE stdout",
           "stdout-7be14d" in str(r5row.get("stdout", "")), True)
    report("  invoking a suite the way a suite is invoked",
           r5row.get("argv"), [sys.executable, str(probe)])
    report("  measuring a real duration", float(r5row.get("duration_s", 0)) > 0, True)
    report("  and writing no ledger into the temp dir",
           (repo / "workspace" / "red-on-main.jsonl").exists(), False)

    # ---- 6. a suite that exits 0 records nothing through the default path either
    probe.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    rows6 = []
    D.record_red_verdict("zzz_t1325_probe.py", "p6-probe", repo=repo, append=rows6.append)
    report("6 a suite that exits 0 records no row", rows6, [])

# ---- 7. THE DECAY TEST: every red verdict in main_red_hold must reach the recorder.
# inspect.getsource proves the call is inside the function that makes the verdict; a whole-file
# substring would pass with the call anywhere. The order matters: the verdict row precedes the
# OPEN row that red_on_main.handle writes.
import inspect                                          # noqa: E402
src = inspect.getsource(D.main_red_hold)
report("7 main_red_hold records the verdict", "record_red_verdict(" in src, True)
report("  once per suite it has confirmed red", "record_red_verdict(suite" in src, True)
report("  and before the task-filing row",
       0 <= src.find("record_red_verdict(") < src.find("red_on_main.handle("), True)

# ---- 8. the row lands in the ledger the task-filer already writes, not a new file
ledger = Path(str(getattr(D, "RED_LEDGER", "")))
report("8 the default ledger is workspace/red-on-main.jsonl",
       str(ledger), str(ROOT / "workspace" / "red-on-main.jsonl"))

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
