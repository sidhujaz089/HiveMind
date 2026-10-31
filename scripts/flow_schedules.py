#!/usr/bin/env python3
"""Every scheduled job on this box, on the Flow chart -- including the ones added tomorrow (2026-10-30).

Jas: "Can you make sure all these cron events appear on the flow chart dynamically if they dont already? It needs to
catch the future ones automatically too."

The chart already declares services, tasks, helpers and sources, and `check_stale_services` refuses to register a
service whose node is not there. SCHEDULED work had no such node: `nas-backup.timer`, `corpus-index.timer`,
`restart-stale.timer` and others existed only as prose inside other nodes' text, so a person reading the chart could
not see what runs on a clock, and nothing noticed when that changed.

WHAT THIS DOES, and why it is a PROJECTION rather than a list somebody maintains:
  snapshot()  reads what is actually scheduled -- user timers, system timers, the user crontab and /etc/cron.d --
              and what each one does and when it next runs
  sync()      writes that into FLOW.json's `schedules` block, which is GENERATED, never hand-edited
  problems()  the units that are scheduled but not on the chart, which is what a check FAILS on

So a timer added tomorrow is caught the moment anyone looks: either the hourly checks run `sync` and the chart
describes it, or the invariant check fails and says so. It cannot exist in silence.

    python3 scripts/flow_schedules.py            # what is scheduled, and what the chart is missing
    python3 scripts/flow_schedules.py --sync      # regenerate the chart's schedules block
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FLOW = REPO / "projects" / "local-inference" / "models" / "FLOW.json"
# WHAT COUNTS AS OURS. The box also runs apt, fstrim, logrotate and snap's own timers; those are the operating
# system's schedule, not the HiveMind's, and putting 38 of them on the flow chart would bury the ten that are the
# system's own work. A schedule is OURS when its command runs something from this repository -- which is also what
# catches the future one: a cron entry that calls a repo script is ours the moment it exists.
OURS = (str(REPO), "ai-workspace", "hivemind-tmp")


def sh(*args, timeout=60):
    try:
        r = subprocess.run(list(args), capture_output=True, text=True, errors="replace", timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:               # noqa: BLE001 -- said, not swallowed
        print(f"  NOTE: {' '.join(args)} could not run ({type(e).__name__}: {e})", file=sys.stderr)
        return ""
    return r.stdout


def ours(text):
    """True when a scheduled command runs something from this repository."""
    t = str(text or "")
    return any(marker in t for marker in OURS)


def _units_from_systemd(scope_args, kind):
    """[(id, what, when, next_run)]. A unit with no next run is still scheduled and still listed."""
    out = sh("systemctl", *scope_args, "list-units", "--type=timer", "--all", "--no-legend", "--no-pager")
    ids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(".timer"):
            ids.append(parts[0])
    rows = []
    for unit in sorted(set(ids)):
        # ExecStart lives on the SERVICE a timer triggers, not on the timer. Asking the timer for it returns
        # nothing, which silently filtered out every systemd schedule of ours on the first run.
        show = sh("systemctl", *scope_args, "show", unit, "-p", "TimersCalendar", "-p", "Description",
                  "-p", "NextElapseUSecRealtime", "-p", "NextElapseUSecMonotonic", "-p", "Unit")
        unit_props = {}
        for line in show.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                unit_props[k.strip()] = v.strip()
        svc = unit_props.get("Unit") or unit[:-6] + ".service"
        show += sh("systemctl", *scope_args, "show", svc, "-p", "ExecStart")
        props = {}
        for line in show.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
        if not ours(props.get("ExecStart", "")):
            continue                                   # the operating system's schedule, not this system's
        calendar = props.get("TimersCalendar") or props.get("NextElapseUSecRealtime") or ""
        when = calendar.split(";")[0].replace("OnCalendar=", "").strip().strip("{}").strip() if calendar else ""
        rows.append({"id": unit, "kind": kind, "what": props.get("Description", ""), "when": when,
                     "next": props.get("NextElapseUSecRealtime") or props.get("NextElapseUSecMonotonic") or ""})
    return rows


def _cron_id(when, command):
    """A stable, READABLE id for a crontab line: the script it runs, else its first word.

    "cron:*/2-*-*-*-*" told a reader nothing about which job was missing from the chart; the point of putting
    these on the chart is that a person can see what runs and when.
    """
    tokens = [t for t in command.split() if not t.startswith("-") and "=" not in t] or command.split()[:1]
    scripts = [Path(t).name for t in tokens if t.endswith((".py", ".sh"))]
    if scripts:
        return f"cron:{scripts[-1]}"
    # a "cd <repo> && ..." line names the repo first; the job is whatever it actually runs
    names = [Path(t).name for t in tokens if "/" in t and Path(t).name not in REPO.name]
    return f"cron:{(names or [Path(tokens[0]).name if tokens else 'unnamed'])[0]}"


def _crontab_rows():
    """The user crontab and /etc/cron.d, as (id, what, when). An unreadable one is said, not skipped."""
    rows = []
    text = sh("crontab", "-l")
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) >= 6 and ours(" ".join(parts[5:])):
            row = {"id": _cron_id(parts[:5], " ".join(parts[5:])), "kind": "cron-user",
                   "what": " ".join(parts[5:]), "when": " ".join(parts[:5]), "next": ""}
            if row["id"] in {r["id"] for r in rows}:
                row["id"] += "@" + "-".join(parts[:5]).replace("*", "x").replace(",", "_").replace("/", "-")
            rows.append(row)
    d = Path("/etc/cron.d")
    if d.is_dir():
        for f in sorted(d.glob("*")):
            try:
                body = f.read_text(encoding="utf-8", errors="replace")
            except OSError as e:                                    # noqa: BLE001 -- said, not swallowed
                print(f"  NOTE: {f} unreadable ({e}) -- not listed", file=sys.stderr)
                continue
            for line in body.splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                parts = s.split()
                if len(parts) >= 7 and ours(" ".join(parts[6:])):  # cron.d carries a user column
                    rows.append({"id": _cron_id(parts[:5], " ".join(parts[6:])),
                                 "kind": "cron-system", "what": " ".join(parts[6:]),
                                 "when": " ".join(parts[:5]), "next": ""})
    return rows


def snapshot():
    """Everything scheduled on this box, from the things that own it."""
    rows = []
    rows += _units_from_systemd(["--user"], "timer-user")
    rows += _units_from_systemd([], "timer-system")
    rows += _crontab_rows()
    # the same unit can be reported twice (system and user scope); keep one, keyed by id
    seen, out = set(), []
    for r in rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        out.append(r)
    return out


def chart_schedules(chart=None):
    """The `schedules` block the chart currently carries, as {id: row}."""
    try:
        d = chart if chart is not None else json.loads(FLOW.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {r.get("id"): r for r in (d.get("schedules") or []) if isinstance(r, dict)}


def problems(live=None, chart=None):
    """[(id, why)] -- scheduled, and the chart does not carry it."""
    live = snapshot() if live is None else live
    known = chart_schedules(chart)
    out = []
    for row in live:
        if row["id"] not in known:
            out.append((row["id"], f"{row['kind']}: {row.get('when') or 'no fixed time'}"))
    return out


def sync(apply=True):
    """Rewrite FLOW.json's `schedules` block from what is actually scheduled. Returns the rows written."""
    live = snapshot()
    path = FLOW
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:                               # noqa: BLE001 -- said, not swallowed
        print(f"flow_schedules: cannot read {path} ({type(e).__name__}: {e})", file=sys.stderr)
        return []
    if d.get("schedules") == live:
        return live                                     # nothing changed: no row, no churn
    before = json.loads(path.read_text(encoding="utf-8"))
    d["schedules"] = live
    if apply:
        # ONE WRITER FOR THE CHART (2026-10-30). Writing FLOW.json here directly broke the changelog's chain:
        # row 112's `before` was no longer row 111's `after`, and test_changelog_chain said so in as many words --
        # "the chart was changed outside flow_change.py, and the changelog cannot be replayed past it". The
        # projection is a change to the chart like any other, so it goes through flow_change's writer and gets a
        # row that carries its before and after.
        sys.path.insert(0, str(REPO / "scripts"))
        import flow_change as fc
        fc._write(d, before, "dsh-harness",
                  f"schedule projection: {len(live)} scheduled job(s) on this box",
                  task="", ops=None)
    return live


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    live = snapshot()
    missing = problems(live)
    print(f"  {len(live)} scheduled job(s) on this box; the chart carries {len(chart_schedules())}")
    for row in live:
        mark = "  " if row["id"] not in {m[0] for m in missing} else "??"
        print(f"  {mark} {row['id']:36s} {row['kind']:12s} {row['when'] or '(no fixed time)'}")
    if "--sync" in argv:
        written = sync(apply=True)
        print(f"  wrote {len(written)} row(s) into {FLOW.relative_to(REPO)}")
        return 0
    if missing:
        print(f"\n  {len(missing)} scheduled job(s) are NOT on the chart:")
        for i, why in missing:
            print(f"    {i}  ({why})")
        print("  python3 scripts/flow_schedules.py --sync")
        return 1
    print("  every scheduled job is on the Flow chart")
    return 0


if __name__ == "__main__":
    sys.exit(main())
