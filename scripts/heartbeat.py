#!/usr/bin/env python3
"""A running job publishes its own liveness as a FILE. Nothing has to ask it.

WHY (t273, and four instances on 2026-09-11). Jas twice said "I still don't see a background
task running" while two dispatches were alive. The harness panel lists only tracked tasks, so
a detached job is invisible; and the tracked WAITERS completed one by one while the jobs ran
on, draining the panel to empty. He was watching an accurate signal that had stopped being
connected to anything, which is worse than no signal.

PROCESS LIVENESS LIES HERE. `pgrep -f` matches the shell that is running it, so a dead job
reads as alive (L-105). The guard hook in this repository blocks that pattern outright. An
ARTIFACT cannot match the thing looking for it and cannot be a zombie: a file whose mtime
stops advancing is a job that stopped, and that is true whether it exited, hung, or was
killed.

SO: a job touches its beat file as it works, and any supervisor -- Gemma, a Haiku session,
Jas -- reads the directory. No process table, no self-report, no asking the party that wants
to be thought alive.

THREE STATES, NEVER TWO. running / STALE / done. A supervisor that can only distinguish
"file exists" from "file absent" cannot tell a finished job from a hung one, which is the
distinction the whole thing is for.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BEATS = REPO / "workspace" / "beats"


def beat(name, note="", state="running", pid=None, extra_fields=None):
    """Write or refresh one job's beat. Cheap enough to call in a loop."""
    BEATS.mkdir(parents=True, exist_ok=True)
    p = BEATS / f"{name}.json"
    prior = {}
    if p.exists():
        try:
            prior = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            prior = {}
    row = {"name": name, "state": state, "note": note,
           "pid": pid if pid is not None else os.getpid(),
           "started": prior.get("started") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "beat": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "beats": (prior.get("beats") or 0) + 1}
    if extra_fields:
        row.update(extra_fields)
    p.write_text(json.dumps(row, indent=1), encoding="utf-8")
    return row


def watch(name, watched):
    """A watcher publishes a beat describing what it observes about a watched job."""
    watched_p = BEATS / f"{watched}.json"
    note = ""
    if watched_p.exists():
        try:
            w_row = json.loads(watched_p.read_text(encoding="utf-8"))
            w_name = w_row.get("name", watched)
            w_beat = w_row.get("beat")
            w_age = int(time.time() - watched_p.stat().st_mtime)
            note = f"{w_name}, beat {w_beat}, {w_age} sec"
        except (json.JSONDecodeError, ValueError):
            note = f"watched {watched} is unreadable"
    else:
        note = f"watched {watched} absent"
    return beat(name, note=note, extra_fields={"watched": watched})


def status(stale_after=600):
    """Every job's state, judged by the CLOCK and not by what the job says about itself.

    A job that wrote state=running and then died still says running. Only the mtime knows.
    """
    out = []
    if not BEATS.is_dir():
        return out
    now = time.time()
    for p in sorted(BEATS.glob("*.json")):
        try:
            row = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, ValueError):
            # A BEAT FILE THAT CANNOT BE READ IS NOT A MISSING JOB. Reporting it as absent
            # would be the same absence-read-as-success defect this repository keeps finding.
            out.append({"name": p.stem, "state": "UNREADABLE", "age_s": None,
                        "note": "beat file is not valid JSON"})
            continue
        age = now - p.stat().st_mtime
        watched = row.get("watched")
        if watched and not (BEATS / f"{watched}.json").exists():
            state = "ORPHAN"
        else:
            state = row.get("state", "running")
            if state == "running" and age > stale_after:
                state = "STALE"
        out.append({"name": row.get("name", p.stem), "state": state,
                    "age_s": int(age), "beats": row.get("beats"),
                    "started": row.get("started"), "note": row.get("note", "")})
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("beat"); b.add_argument("name"); b.add_argument("--note", default="")
    b.add_argument("--state", default="running",
                   choices=["running", "done", "failed"])
    s = sub.add_parser("status"); s.add_argument("--stale-after", type=int, default=600)
    s.add_argument("--json", action="store_true")
    sub.add_parser("self-test")
    a = ap.parse_args()

    if a.cmd == "beat":
        r = beat(a.name, a.note, a.state)
        print(f"  {r['name']}  {r['state']}  beat {r['beats']}")
        return 0
    if a.cmd == "self-test":
        return self_test()

    rows = status(a.stale_after)
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0
    if not rows:
        # NOT "all clear". Nothing has reported, which is a different fact.
        print("  no beat files -- NOTHING HAS REPORTED. This is not the same as idle.")
        return 0
    for r in rows:
        age = "?" if r["age_s"] is None else f"{r['age_s']}s ago"
        print(f"  [{r['state']:<9}] {r['name']:<34} {age:>9}  {r.get('note','')[:44]}")
    bad = [r for r in rows if r["state"] in ("STALE", "failed", "UNREADABLE")]
    if bad:
        print(f"\n  {len(bad)} job(s) need attention: "
              + ", ".join(f"{r['name']}({r['state']})" for r in bad))
    return 0


def self_test():
    """The gate must report STALE, not merely 'exists'. A two-state supervisor is useless."""
    import tempfile
    global BEATS
    ok = True
    with tempfile.TemporaryDirectory() as td:
        BEATS = Path(td)
        beat("fresh", "working")
        rows = {r["name"]: r for r in status(stale_after=600)}
        c = rows.get("fresh", {}).get("state") == "running"
        ok &= c; print(f"  [{'ok ' if c else 'BAD'}]  a fresh beat reads running")

        # THE ONE THAT MATTERS: a job that says running and stopped beating is STALE.
        beat("zombie", "claims to be working")
        p = BEATS / "zombie.json"
        old = time.time() - 4000
        os.utime(p, (old, old))
        rows = {r["name"]: r for r in status(stale_after=600)}
        c = rows.get("zombie", {}).get("state") == "STALE"
        ok &= c
        print(f"  [{'ok ' if c else 'BAD'}]  a job that stopped beating is STALE, though its "
              f"own file still says running")

        beat("finished", "", state="done")
        rows = {r["name"]: r for r in status()}
        c = rows.get("finished", {}).get("state") == "done"
        ok &= c; print(f"  [{'ok ' if c else 'BAD'}]  a finished job is done, not stale")

        (BEATS / "broken.json").write_text("{not json")
        rows = {r["name"]: r for r in status()}
        c = rows.get("broken", {}).get("state") == "UNREADABLE"
        ok &= c
        print(f"  [{'ok ' if c else 'BAD'}]  an unreadable beat is UNREADABLE, not absent")

        BEATS = Path(td) / "nothing-here"
        c = status() == []
        ok &= c
        print(f"  [{'ok ' if c else 'BAD'}]  no beats at all returns empty, and the CLI says "
              f"'nothing has reported' rather than 'idle'")
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
