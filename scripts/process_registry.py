#!/usr/bin/env python3
"""Record every change to the box's standing processes: cron lines and enabled user units (t643).

WHY. Ad-hoc 15-minute Gemma/Q16 monitoring timers were created in a session, never put on the Flow
chart, and later removed with their .sh files -- and nobody could say who removed them, when, or what
replaced them (RCA 087bf5c126bf, Rule 4 case 2: no collector at that endpoint). check_workspace now
FAILS on an off-chart unit or cron line, but a failure says what is wrong NOW, not what changed.

WHAT. Each dispatcher tick: snapshot `crontab -l` (non-comment lines) and every enabled user unit
(name, and whether it runs this repository's code), compare with the last snapshot, and append ONE
row to workspace/process-changes.jsonl when anything was added or removed -- with whether each added
item is on the Flow chart. No change, no row. Never raises: a recorder must not take the tick down.

    python3 scripts/process_registry.py          # record now; print what changed
    python3 scripts/process_registry.py --show   # the last 10 changes
"""
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SNAP = REPO / "workspace" / "run-logs" / "process-registry.json"
LEDGER = REPO / "workspace" / "process-changes.jsonl"
FLOW = REPO / "projects" / "local-inference" / "models" / "FLOW.json"
UNITS = Path.home() / ".config" / "systemd" / "user"
SYSTEM_UNITS = Path("/etc/systemd/system")  # system-scope units, e.g. the Ollama installer's ollama.service (RCA 50c99d891199 D1)


def snapshot(run=subprocess.run):
    """{"cron": [lines], "units": {unit: runs_repo_code}} -- what is standing right now."""
    cron = run(["crontab", "-l"], capture_output=True, text=True, timeout=30).stdout
    lines = sorted(l.strip() for l in cron.splitlines() if l.strip() and not l.lstrip().startswith("#"))
    out = run(["systemctl", "--user", "list-unit-files", "--state=enabled", "--type=service", "--no-legend"],
              capture_output=True, text=True, timeout=30).stdout
    system = run(["systemctl", "list-unit-files", "--state=enabled", "--type=service", "--no-legend"],
                 capture_output=True, text=True, timeout=30).stdout
    units = {}
    for scope, text in (("user:", out), ("system:", system)):
        base = UNITS if scope == "user:" else SYSTEM_UNITS
        for line in text.splitlines():
            if not line.strip():
                continue
            name = line.split()[0]
            f = base / name
            try:
                units[scope + name] = f.is_file() and str(REPO) in f.read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                print(f"  NOTE: {f} unreadable ({e}); recorded as not running repo code", file=sys.stderr)
                units[scope + name] = False
    return {"cron": lines, "units": units}



def on_chart(item, flow_text):
    """Is this cron line or unit named on the Flow chart?"""
    import re
    if item.startswith("cron: "):
        m = re.search(r"([\w./-]+\.(?:py|sh))", item)
        return bool(m) and ('"' + m.group(1) + '"') in flow_text
    name = item
    if name.startswith("user:") or name.startswith("system:"):
        name = name.split(":", 1)[1]
    stem = name
    for ext in (".service", ".timer", ".socket"):
        if name.endswith(ext):
            stem = name[:-len(ext)]
            break
    return ('"' + name + '"') in flow_text or ('"' + stem + '"') in flow_text



def diff(old, new):
    """(added, removed) as 'cron: <line>' / '<unit>' strings."""
    def items(s):
        return {f"cron: {l}" for l in s.get("cron", [])} | set(s.get("units", {}))
    a, b = items(old or {}), items(new)
    return sorted(b - a), sorted(a - b)


def record(snap=None, prev=None, flow_text=None, write=True):
    """Append a change row if the standing processes changed; return it (or None)."""
    snap = snap if snap is not None else snapshot()
    if prev is None:
        try:
            prev = json.loads(SNAP.read_text(encoding="utf-8"))
        except FileNotFoundError:
            prev = None                       # first run: the baseline, not a change
        except (OSError, ValueError) as e:
            print(f"  NOTE: {SNAP} unreadable ({e}); taking a new baseline", file=sys.stderr)
            prev = None
    row = None
    if not snap.get("units"):
        print("  NOTE: snapshot has no units; the census saw no units, so no change row is written", file=sys.stderr)
        return None
    if prev is not None:
        added, removed = diff(prev, snap)
        if added or removed:
            if flow_text is None:
                flow_text = FLOW.read_text(encoding="utf-8")
            row = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "added": added,
                   "removed": removed, "off_chart": [a for a in added if not on_chart(a, flow_text)]}
    if write:
        SNAP.parent.mkdir(parents=True, exist_ok=True)
        SNAP.write_text(json.dumps(snap, indent=1) + "\n", encoding="utf-8")
        if row:
            with LEDGER.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
    return row


def main(argv):
    if "--show" in argv:
        rows = LEDGER.read_text(encoding="utf-8").splitlines() if LEDGER.exists() else []
        print("\n".join(rows[-10:]) or "no changes recorded yet")
        return 0
    row = record()
    print(json.dumps(row) if row else "no change in cron lines or enabled user units")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
