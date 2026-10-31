#!/usr/bin/env python3
"""The pre-commit/merge gate's timeout is a SETTING, and every run records its own duration (Jas, 2026-10-31).

Jas: *"I want that 1800 in the settings section where I can adjust it plus a recording of run actual times and a
number of runs that timed out."*

WHY A FILE AND NOT A CONSTANT. The number lives in two guarded places (the hook and the merge path). Making it a save
in `workspace/gate-settings.json` means adjusting it is a page edit, not a commit to a guarded file with a second
signature -- which is the difference between a number that gets tuned and one that gets argued about.

WHY THE LEDGER IS WRITTEN WHERE THE GATE RUNS. Recording only at session end measured close-outs and nothing else,
while the hook and the merge run the same gate hundreds of times -- that is where the distribution actually is. Each
run records its duration, its exit code, whether it hit the cap, and where it ran.

    python3 scripts/gate_settings.py                     # the setting, the runs, the timeouts
    python3 scripts/gate_settings.py set 1800             # save a new cap
    python3 scripts/gate_settings.py --json              # the rows, for a page to plot
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SETTINGS = Path(os.environ.get("HIVEMIND_GATE_SETTINGS") or (REPO / "workspace" / "gate-settings.json"))
RUNS = REPO / "workspace" / "gate-runs.jsonl"
DEFAULT_TIMEOUT_S = 1800.0
TIMEOUT_RC = 124

WHY = (
    "INTERIM (Jas, 2026-10-31): '900 is too short and if we don't have data, make the timeout 30 minutes and see if "
    "that captures most things. I want session ends to generate data that we can see on a graph. If we see outliers "
    "that is the point we target our CI.' The 900 it replaced (1007505b9, 2026-10-02) had no derivation and was "
    "smaller than the work it bounds -- related_tests.py caps each SUITE at 300 s. This value is replaced by the p99 "
    "of workspace/gate-runs.jsonl once that ledger has 30+ completed runs, rounded up to the next 5 minutes."
)


def timeout_default():
    """The cap when nothing has been saved. One function, so a page and a script cannot disagree."""
    return DEFAULT_TIMEOUT_S


def timeout_s(path=None):
    """The saved cap, or the default. A corrupt file falls back and SAYS so -- it never becomes a silent cap."""
    p = Path(path) if path else SETTINGS
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except OSError:
        return timeout_default()
    except ValueError as e:
        print(f"  NOTE: {p} is not JSON ({e}) -- using the {timeout_default():g} s default", file=sys.stderr)
        return timeout_default()
    try:
        val = float(data.get("timeout_s"))
    except (TypeError, ValueError):
        print(f"  NOTE: {p} has no usable timeout_s (it says {data.get('timeout_s')!r}) -- using the "
              f"{timeout_default():g} s default", file=sys.stderr)
        return timeout_default()
    if val <= 0:
        print(f"  NOTE: {p} says {val:g} s, which cannot bound anything -- using the default", file=sys.stderr)
        return timeout_default()
    return val


def save(seconds, path=None, why=""):
    """(ok, message). The one writer of the setting, so a page and a script cannot race into two formats."""
    p = Path(path) if path else SETTINGS
    try:
        seconds = float(seconds)
    except (TypeError, ValueError):
        return False, f"{seconds!r} is not a number of seconds"
    if seconds <= 0:
        return False, "a cap has to be a positive number of seconds"
    row = {"timeout_s": seconds, "why": why or "set by hand", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "_what": "The gate's wall-clock cap, in seconds. Read by scripts/hooks/pre-commit and scripts/merge_reviewed.py.",
           "_interim": WHY}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(row, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as e:                                           # noqa: BLE001 -- said, not swallowed
        return False, f"could not write {p}: {type(e).__name__}: {e}"
    return True, f"saved {seconds:g} s to {p.name}"


def record_run(seconds, rc, where, path=None):
    """Append one gate run. Best effort: a gate must never fail because its telemetry could not be written."""
    p = Path(path) if path else RUNS
    row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "seconds": round(float(seconds), 1),
           "rc": int(rc) if rc is not None else None, "timed_out": int(rc if rc is not None else -1) == TIMEOUT_RC,
           "where": str(where or "")}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
        return True
    except OSError as e:                                           # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: gate run not recorded ({type(e).__name__}: {e})", file=sys.stderr)
        return False


def load_runs(path=None):
    p = Path(path) if path else RUNS
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as e:
            print(f"  NOTE: {p.name} line {i} is not JSON ({e}) -- skipped", file=sys.stderr)
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def summary(rows):
    """{runs, timed_out, completed, p50, p95, max, total_s} -- the numbers a person and a page both need."""
    secs = sorted(float(r.get("seconds") or 0) for r in (rows or []))
    timeouts = sum(1 for r in (rows or []) if r.get("timed_out"))
    if not secs:
        return {"runs": 0, "timed_out": 0, "completed": 0, "p50": 0.0, "p95": 0.0, "max": 0.0, "total_s": 0.0}

    def at(q):
        return secs[min(len(secs) - 1, int(q * (len(secs) - 1)))]
    return {"runs": len(secs), "timed_out": timeouts, "completed": len(secs) - timeouts,
            "p50": round(at(0.50), 1), "p95": round(at(0.95), 1), "max": round(secs[-1], 1),
            "total_s": round(sum(secs), 1)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="The gate's timeout setting, and what its runs actually cost.")
    ap.add_argument("action", nargs="?", choices=["show", "set", "get", "record"], default="show")
    ap.add_argument("value", nargs="?", help="seconds, for 'set' and 'record'")
    ap.add_argument("extra", nargs="*", help="record: <rc> <where>")
    ap.add_argument("--why", default="", help="recorded with the new value")
    ap.add_argument("--json", action="store_true", help="the run rows themselves")
    a = ap.parse_args(argv)
    # 'get' EXISTS FOR THE SHELL, AND ITS ABSENCE WAS A REAL DEFECT (2026-10-31). The pre-commit hook called
    # `gate_settings.py get`, the subcommand did not exist, argparse exited 2, the hook's `|| echo 1800` hid it, and the
    # hook therefore NEVER read the setting -- always 1800. Claude found it; a probe with the setting at 3600 captured
    # 1800. One number, printed alone, so the hook needs no sed and cannot parse a report by accident.
    if a.action == "get":
        # THE SHELL NEEDS TO KNOW WHY IT GOT THIS NUMBER (2026-10-31). Printing "1800" for both "the setting says
        # 1800" and "there is no setting" is the silence this whole change exists to remove: the hook fell back and
        # nobody could tell. 0 means the setting was read; 3 means there is none; 4 means it could not be used.
        if not SETTINGS.is_file():
            print(f"{timeout_default():g}")
            print(f"  NOTE: no saved setting at {SETTINGS} -- using the {timeout_default():g} s default", file=sys.stderr)
            return 3
        val = timeout_s()
        print(f"{val:g}")
        try:
            saved = float(json.loads(SETTINGS.read_text(encoding="utf-8")).get("timeout_s"))
        except (OSError, ValueError, TypeError, AttributeError):
            saved = None
        if saved != val:
            return 4          # timeout_s() has already said why; a second note for one fallback is noise
        return 0
    # 'record' exists so a shell hook can log its own run without importing python inline.
    if a.action == "record":
        if a.value is None:
            ap.error("record needs <seconds>")
        rc = int(a.extra[0]) if a.extra else 0
        where = a.extra[1] if len(a.extra) > 1 else "hook"
        return 0 if record_run(a.value, rc, where) else 1
    if a.action == "set":
        if a.value is None:
            ap.error("set needs a number of seconds")
        ok, msg = save(a.value, why=a.why)
        print(f"  {msg}")
        return 0 if ok else 1
    if a.json:
        print(json.dumps(load_runs(), indent=1))
        return 0
    rows = load_runs()
    s = summary(rows)
    print(f"  gate timeout : {timeout_s():g} s  (source: {'saved' if SETTINGS.is_file() else 'default'})")
    print(f"  runs recorded: {s['runs']}  ({s['timed_out']} timed out, {s['completed']} completed)")
    if s["runs"]:
        print(f"  seconds      : p50 {s['p50']:.1f}  p95 {s['p95']:.1f}  max {s['max']:.1f}")
        if s["timed_out"]:
            share = 100.0 * s["timed_out"] / s["runs"]
            print(f"  NOTE: {s['timed_out']} of {s['runs']} runs ({share:.0f}%) hit the cap -- "
                  f"a cap that is hit often is the wrong cap, and the runs over it are the CI target")
    else:
        print("  seconds      : no runs recorded yet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
