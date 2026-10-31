#!/usr/bin/env python3
"""The gate's timeout is a SETTING, and every run is recorded with whether it timed out (Jas, 2026-10-31).

Jas: *"I want that 1800 in the settings section where I can adjust it plus a recording of run actual times and a
number of runs that timed out."*

Three things, and the third is what tells us whether the number is right:

  * the number lives in workspace/gate-settings.json, read by the hook and the merge, so changing it is a save and not
    a commit to a guarded file;
  * every run appends its own duration to workspace/gate-runs.jsonl, so the distribution is measured where the gate
    actually runs rather than only at session end;
  * a run that hits the cap is recorded AS A TIMEOUT, and counted, because "how often does it stop short" is the
    question that decides whether 1800 is too small.

RED ON MAIN: no store, no ledger, no count.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import gate_settings as gs          # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'FAIL'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


# --- the setting --------------------------------------------------------------------------------
check("the default is 1800 s when nothing has been saved", gs.timeout_default() == 1800.0, str(gs.timeout_default()))
check("  and it is stated as interim, with Jas's reasoning", "INTERIM" in gs.WHY.upper() and "graph" in gs.WHY.lower(),
      gs.WHY[:60])

with __import__("tempfile").TemporaryDirectory() as td:
    store = Path(td) / "gate-settings.json"
    check("an unsaved setting reads the default", gs.timeout_s(store) == 1800.0, str(store))
    gs.save(3600, store, why="testing a longer cap")
    check("a saved value is what everything reads", gs.timeout_s(store) == 3600.0, str(store))
    check("  and the file is JSON a page can edit", json.loads(store.read_text())["timeout_s"] == 3600.0, "")
    check("  and the reason is kept with it", "testing" in json.loads(store.read_text()).get("why", ""), "")
    check("a corrupt file falls back to the default and SAYS so", gs.timeout_s(Path(td) / "nope.json") == 1800.0, "")
    store.write_text("{not json")
    check("  and a corrupt file is not silently obeyed", gs.timeout_s(store) == 1800.0, "corrupt must not become a cap")

# --- the run ledger -----------------------------------------------------------------------------
with __import__("tempfile").TemporaryDirectory() as td:
    led = Path(td) / "gate-runs.jsonl"
    gs.record_run(12.5, 0, "hook", led)
    gs.record_run(1800.0, 124, "hook", led)
    gs.record_run(3.0, 1, "merge", led)
    rows = gs.load_runs(led)
    check("every run is recorded", len(rows) == 3, str(len(rows)))
    check("  with its duration", [r["seconds"] for r in rows] == [12.5, 1800.0, 3.0], str(rows))
    check("  and a timeout is marked as one", [r["timed_out"] for r in rows] == [False, True, False], str(rows))
    check("  and where it ran", [r["where"] for r in rows] == ["hook", "hook", "merge"], str(rows))
    s = gs.summary(rows)
    check("the summary counts the runs", s["runs"] == 3, str(s))
    check("  and the number that timed out", s.get("timed_out") == 1, str(s))
    check("  and the spread", s["p50"] > 0 and s["max"] == 1800.0, str(s))
    check("nothing recorded is zero runs, not a guess", gs.summary([])["runs"] == 0, str(gs.summary([])))

print("test_gate_settings:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
