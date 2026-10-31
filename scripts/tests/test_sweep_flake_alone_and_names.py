#!/usr/bin/env python3
"""t1885 -- a surprising sweep failure is believed only after it fails ALONE, and its names survive.

Measured 2026-10-25 (dsh-harness/deepseek-v4.1-flash) from the hourly ledger and a sweep:
(1) A FLAKE IS REPORTED AS A NEW FAILURE. test_coder_yield_resume.py passes 11/0 when run alone and
fails only under the sweep's concurrency, and it is t1167's whole history. coder.py got this fix on
2026-09-16; the sweep had a retry too but ran it INSIDE the worker pool, so it re-ran the suite under
the same load and failed again.
(2) THE NAMES ARE DISCARDED. The last hourly rows read 'sweep: 7 new failure(s)' with 'filed: []' --
the names were computed, used to build a task body, and thrown away because a task for the check
already existed.
(3) NOTHING PROVES THE SWEEP RAN. --assert-fresh exists and nothing called it; a sweep skipped,
killed or silently failed left no finding, and silence reads as health.

Seeded throughout: a fake `once` records when each call ran, so "alone" is an assertion about
overlap rather than a word; hourly_checks' sweep runner and subprocess are replaced for the run.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "projects" / "app-repo")]
import hourly_checks as hc  # noqa: E402
import run_tests as rt  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok else f"  (got {got!r}, want {want!r})"))


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    logf = tmp / "flaky-tests.log"

    # ---- 1. a surprising failure is retried, and only after the pool has drained ---------------
    calls = []

    def fake_once(rc_first=1, rc_retry=0, fail_for=None):
        def run(p):
            name = Path(p).name
            rec = {"name": name, "start": time.monotonic(), "end": None}
            calls.append(rec)
            time.sleep(0.05)
            n = sum(1 for c in calls if c["name"] == name)
            first = n == 1
            rec["end"] = time.monotonic()
            failing = first and (fail_for is None or name in fail_for)
            return (rc_first if failing else rc_retry), 0.05, "output"
        return run

    files = [tmp / f"test_{n}.py" for n in ("a", "b", "c")]
    res = rt.run_light(files, jobs=3, once=fake_once(fail_for={"test_a.py"}), allowed=set(),
                       log=logf)
    wave, retries = calls[:3], calls[3:]
    wave_end = max(c["end"] for c in wave)
    report("every suite of the first wave finished before any retry started",
           all(c["start"] >= wave_end for c in retries), True)
    report("  only the surprising failure was retried", [c["name"] for c in retries], ["test_a.py"])
    report("  and the retries do not overlap one another",
           all(retries[i]["start"] >= retries[i - 1]["end"] for i in range(1, len(retries))), True)
    report("  the flake is reported FLAKY, not failing",
           [r for r in res if r[0] == "test_a.py"][0][4], True)
    report("  and it does not count as a failure", [r[1] for r in res if r[0] == "test_a.py"][0], 0)
    report("  it is recorded where the count accumulates",
           "test_a.py failed under load then passed alone" in logf.read_text(encoding="utf-8"), True)

    # ---- 2. a declared red is NOT retried ------------------------------------------------------
    calls2 = []

    def counting(p):
        calls2.append(Path(p).name)
        return 1, 0.01, "red"

    res2 = rt.retry_alone([("test_known.py", 1, 1.0, "", False)], {"test_known.py"},
                          once=counting, log=logf)
    report("a suite the ceiling already declares is not re-run", calls2, [])
    report("  it stays failing in the result", (res2[0][1], res2[0][4]), (1, False))

    # ---- 3. the sweep's names reach the ledger and an open task --------------------------------
    detail = "New failing suites (not in the ceiling): test_one.py, test_two.py"
    rec = hc.sweep_record({hc.SWEEP: detail}, "2 new failure(s)")
    report("the ledger row carries the sweep's NAMES, not just a count",
           rec.get("sweep_suites"), ["test_one.py", "test_two.py"])
    report("  and the first line of what happened", rec.get("sweep"), "2 new failure(s)")

    state, notes, filed = {}, [], {"id": None}
    def open_task(marker):
        return filed["id"]
    def file_finding(name, detail, head, ts):
        filed["id"] = "t9001"
        return "t9001"
    def note(tid, text):
        notes.append((tid, text))

    kw = dict(head="abc123", ts="2026-10-25T01:00:00Z", open_task=open_task,
              file=file_finding, note=note)
    state = hc.reconcile({hc.SWEEP: detail}, {hc.SWEEP}, state, **kw)
    report("the first run files one task and records the detail",
           (len(notes), filed["id"]), (0, "t9001"))
    state = hc.reconcile({hc.SWEEP: detail}, {hc.SWEEP}, state, **kw)
    report("the same detail on a later run does not spam a note", len(notes), 0)
    changed = "New failing suites (not in the ceiling): test_one.py, test_two.py, test_three.py"
    state = hc.reconcile({hc.SWEEP: changed}, {hc.SWEEP}, state, **kw)
    report("a CHANGED detail is appended to the open task -- the names are not discarded",
           (len(notes), "test_three.py" in notes[-1][1]), (1, True))

    # ---- 4. the sweep is not started when this tree already has a fresh clean sweep ------------
    ran = []

    class FakeProc:
        returncode = 0
        stdout = "  No new failures.\n"
        stderr = ""

    real_run = hc.subprocess.run
    try:
        hc.subprocess.run = lambda *a, **k: ran.append(a[0]) or FakeProc()
        hc.assert_fresh = lambda minutes=None: (True, "a clean sweep ran 3 min ago")
        got, says = hc.run_sweep()
    finally:
        hc.subprocess.run = real_run
    report("a sweep is skipped when this exact tree already has a fresh clean sweep",
           ("".join(ran), says.startswith("skipped: fresh")), ("", True))

    # ---- 5. a sweep that says OK but leaves no fresh record is a finding ----------------------
    try:
        hc.subprocess.run = lambda *a, **k: FakeProc()
        hc.assert_fresh = lambda minutes=None: (False, "no clean sweep of this tree")
        got2, says2 = hc.run_sweep()
    finally:
        hc.subprocess.run = real_run
    report("a sweep that returned 0 but left no fresh record is a finding",
           (bool(got2), "fresh" in json.dumps(got2)), (True, True))

print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
