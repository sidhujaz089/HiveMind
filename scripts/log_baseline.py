#!/usr/bin/env python3
"""What is NEW, or has changed rate, in the logs nobody reads (t229).

    python3 scripts/log_baseline.py            # the report session start prints under section 0b
    python3 scripts/log_baseline.py --json     # machine-readable

WHY (2026-09-07/08): Jas could not fork a session three times and the app said only "Session fork error". The cause
had been sitting in ~/.config/Claude/logs/main.log for two days: 293 EINVALs from one helper, the first at
2026-09-05 14:07:59, where the previous log file had none. Nobody was looking, and an alert on every ERROR line would
have been muted within a week -- 235 [error] lines sit in main.log today. What makes a line worth a look is that it
is NEW or its RATE MOVED. So this compares the last 24 hours against the 7 days before, per normalised message.

Sources, two first as the task says: the desktop app's own logs (~/.config/Claude/logs/main*.log, [error] and
[warn] lines) and the user journal at priority err (`journalctl --user -p err`). It is a sibling of error_scan.py,
not a mode of it, because error_scan's contract forbids a subprocess and the journal needs one.

Reports; never fixes. No model. A source that cannot be read is SAID, not counted as quiet.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

DESKTOP = Path.home() / ".config" / "Claude" / "logs"
RECENT_S, BASELINE_S = 24 * 3600, 7 * 24 * 3600
SPIKE_X, SPIKE_MIN = 5, 5
SHOWN = 5

# The app names its own version at [info] ("Falling back to installed version: X"), which _DESK's
# [error|warn] filter discards -- the evidence a 30-day pin was later hand-grepped out of. Matched against
# the RAW line, never through _DESK.
VERSION_RX = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d \[(\w+)\] Falling back to installed version:\s*(\d+\.\d+\.\d+)")
DEFAULT_LEDGER = Path(__file__).resolve().parents[1] / "workspace" / "ledgers" / "toolchain-versions.jsonl"
INSTALL_ROOT = DESKTOP.parent / "versions"

_DESK = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[(error|warn)\] (.*)$")
_JOUR = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)([+-]\d\d:?\d\d)? \S+ ([^:]+): (.*)$")
_NORM = [(re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I), "<uuid>"),
         (re.compile(r"\b0x[0-9a-f]+\b|\b[0-9a-f]{12,}\b", re.I), "<hex>"),
         (re.compile(r"\[\d+\]"), "[<pid>]"),
         (re.compile(r"\d+"), "#")]


def key(msg):
    """The message with its variable parts (ids, numbers, pids) replaced, so repeats of one problem count once."""
    for rx, rep in _NORM:
        msg = rx.sub(rep, msg)
    return " ".join(msg.split())


def _epoch(stamp, fmt):
    try:
        return time.mktime(time.strptime(stamp, fmt))
    except ValueError:
        return None


def desktop_events(folder=DESKTOP):
    """[(epoch, source, line)] for [error]/[warn] lines in the app's main*.log files, or None if unreadable."""
    files = sorted(Path(folder).glob("main*.log"))
    if not files:
        return None
    out = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  NOTE: {f} unreadable ({e})", file=sys.stderr)
            continue
        for line in text.splitlines():
            m = _DESK.match(line)
            if m:
                t = _epoch(m.group(1), "%Y-%m-%d %H:%M:%S")
                if t:
                    out.append((t, "desktop-app", f"[{m.group(2)}] {m.group(3)}"))
    return out


def journal_events(run=subprocess.run):
    """[(epoch, source, line)] from the user journal at priority err, the last 8 days; None if it cannot be read."""
    try:
        p = run(["journalctl", "--user", "-p", "err", "--since", "-8d", "-o", "short-iso", "--no-pager"],
                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"  NOTE: journalctl unavailable ({type(e).__name__})", file=sys.stderr)
        return None
    if p.returncode != 0:
        return None
    out = []
    for line in p.stdout.splitlines():
        m = _JOUR.match(line)
        if m:
            t = _epoch(m.group(1), "%Y-%m-%dT%H:%M:%S")
            if t:
                out.append((t, "journal", f"{m.group(3)}: {m.group(4)}"))
    return out


def diff(events, now=None):
    """[{source, kind (new|spike), recent, baseline_per_day, example}] -- newest-rate first."""
    now = now or time.time()
    groups = {}
    for t, src, line in events:
        if t < now - RECENT_S - BASELINE_S or t > now:
            continue
        g = groups.setdefault((src, key(line)), {"recent": 0, "base": 0, "example": line, "newest": t})
        if t >= g["newest"]:
            g["newest"], g["example"] = t, line  # the newest instance, whole (never truncated: error_scan rule 1)
        if t >= now - RECENT_S:
            g["recent"] += 1
        else:
            g["base"] += 1
    out = []
    for (src, _k), g in groups.items():
        per_day = g["base"] / 7
        if g["recent"] and not g["base"]:
            kind = "new"
        elif g["recent"] >= max(SPIKE_MIN, SPIKE_X * per_day):
            kind = "spike"
        else:
            continue
        out.append({"source": src, "kind": kind, "recent": g["recent"], "baseline_per_day": round(per_day, 1),
                    "example": g["example"]})
    return sorted(out, key=lambda x: (-x["recent"], x["source"]))


def report(desk=None, jour=None, now=None):
    """(lines, findings). Each unreadable source is named -- 'quiet' and 'not looked at' never read the same."""
    desk = desktop_events() if desk is None else desk
    jour = journal_events() if jour is None else jour
    unread = [n for n, ev in (("desktop app logs", desk), ("user journal", jour)) if ev is None]
    found = diff([e for ev in (desk, jour) if ev for e in ev], now)
    lines = []
    if found:
        lines.append(f"  {len(found)} log message(s) new or spiking in the last 24 h (vs the 7 days before):")
        for f in found[:SHOWN]:
            lines.append(f"  [{f['kind']}] {f['source']}: {f['recent']} in 24 h (was {f['baseline_per_day']}/day)")
            lines.append(f"      {f['example']}")
        if len(found) > SHOWN:
            lines.append(f"  ... {len(found) - SHOWN} more: python3 scripts/log_baseline.py")
    elif not unread:
        lines.append("  logs: nothing new or spiking in the last 24 h (desktop app, user journal).")
    if unread:
        lines.append("  NOT READ (this is not 'quiet'): " + ", ".join(unread))
    return lines, found


def version_state(folder=DESKTOP, install_root=None, now=None):
    """An ABSOLUTE version row: what is installed, what the app says it is running, and the verdict.

    The existing detector is a rate delta, structurally blind to a condition that is constant for a month
    (RCA 11c4f849789b: pinned to 2.1.237 from 2026-09-05 to 2026-10-07, proved 30 days later by hand).
    State replaces rate: installed_versions from the install directory against the highest version the app
    named in its own logs, compared as tuples of ints -- 2.1.9 installed against 2.1.100 seen is PINNED,
    which a string compare calls CURRENT. No evidence reads UNKNOWN, never CURRENT.
    """

    def vkey(v):
        return tuple(int(p) for p in v.split("."))

    def installed(d):
        # A version directory with nothing in it is a failed download, not an installed version (2026-09-05).
        try:
            return any(p.is_file() for p in d.iterdir())
        except OSError as e:
            print(f"  NOTE: {d} unreadable ({e})", file=sys.stderr)
            return False

    root = Path(INSTALL_ROOT if install_root is None else install_root)
    try:
        entries = sorted(root.iterdir())
    except OSError as e:
        print(f"  NOTE: {root} unreadable ({e})", file=sys.stderr)
        entries = []
    versions = [d.name for d in entries if re.fullmatch(r"\d+\.\d+\.\d+", d.name) and installed(d)]
    versions.sort(key=vkey)

    try:
        files = sorted(Path(folder).glob("main*.log"))
    except OSError as e:
        print(f"  NOTE: {folder} unreadable ({e})", file=sys.stderr)
        files = []
    if not files:
        print(f"  NOTE: {folder} has no main*.log (this is not 'quiet')", file=sys.stderr)

    newest = active = level = source = highest = None
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"  NOTE: {f} unreadable ({e})", file=sys.stderr)
            continue
        for line in text.splitlines():
            m = VERSION_RX.match(line)
            if not m:
                continue
            # line[:19] is the fixed-width leading timestamp field, not a cut record.
            stamp = _epoch(line[:19], "%Y-%m-%d %H:%M:%S")
            t = -1.0 if stamp is None else stamp
            if highest is None or vkey(m.group(2)) > vkey(highest):
                highest = m.group(2)
            if newest is None or t >= newest:  # the NEWEST line supplies both version and level
                newest, active, level, source = t, m.group(2), m.group(1), str(f)

    if active is None:
        highest = None
        verdict = "UNKNOWN"
    elif not versions:
        verdict = "UNKNOWN"  # installed files alone never prove what is RUNNING
    else:
        verdict = "PINNED" if vkey(highest) > vkey(versions[-1]) else "CURRENT"

    if now is None:
        now = time.time()
    return {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "harness": "claude-desktop",
            "installed_versions": versions,
            "active_version": active,
            "highest_version_seen": highest,
            "source": source,
            "level": level,
            "verdict": verdict}


def read_version_rows(path):
    """(rows, bad). Every unparseable non-blank line is returned WHOLE: a run killed mid-write leaves a torn
    final line, and that fragment is evidence, not a corrupted ledger -- it never takes the rows before it down.
    """
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return [], []
    except OSError as e:
        print(f"  NOTE: {path} unreadable ({e})", file=sys.stderr)
        return [], []
    rows, bad = [], []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        try:
            rows.append(json.loads(raw))
        except ValueError:
            bad.append(raw)
    return rows, bad


def append_version_row(path, row):
    """Append one state row, whole: no slice, no cap, no truncation of any field."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


def today_row(path, date_str):
    """The ledger row whose ts starts with date_str (YYYY-MM-DD), last write winning, else None."""
    found = None
    for row in read_version_rows(path)[0]:
        if isinstance(row, dict) and str(row.get("ts", "")).startswith(date_str):
            found = row
    return found


def main(argv):
    lines, found = report()
    if "--json" in argv:
        print(json.dumps(found, indent=1))
    else:
        print("\n".join(lines))
        if len(found) > SHOWN:
            for f in found[SHOWN:]:
                print(f"  [{f['kind']}] {f['source']}: {f['recent']} in 24 h (was {f['baseline_per_day']}/day)\n"
                      f"      {f['example']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
