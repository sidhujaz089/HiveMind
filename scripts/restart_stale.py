#!/usr/bin/env python3
"""Restart a service that is running code older than the code on disk.

WHY THIS IS NOT A NOTE TO MYSELF. I edited staffing_run.py twice while its process sat
waiting, and both times the running service kept executing the old version -- once watching a
path that no longer existed in the design, once without the calibration step entirely. The
stale-process check caught both, and both times my fix was "remember to restart it". Jas:
"If you can automate that instead of relying on memory, do that." A rule I have to remember is
a rule I have already broken twice.

WHAT IT WILL AND WILL NOT TOUCH, AND THE DISTINCTION IS THE WHOLE DESIGN.

RESTART-SAFE means the process is a server or a waiter: it holds no work in progress, so
restarting costs a few seconds and loses nothing. Those are listed explicitly below. Adding a
service to that list is a claim that interrupting it at any moment is harmless.

Everything else is REPORTED AND LEFT ALONE. coder.py is the case that matters: it carries a
build in flight, and restarting it to satisfy a staleness check would
destroy hours of work to fix a cosmetic complaint. An automation that cannot tell those apart
should not be running unattended.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOG = REPO / "workspace" / "restart-stale.log"
# Servers and waiters. Each entry claims: interrupting this at any moment loses nothing.
SAFE = {
    "model-chat.service":    REPO / "projects" / "local-inference" / "chatui.py",
    "agent-console.service": REPO / "projects" / "local-inference" / "console.py",
    "app-feedback.service":  REPO / "projects" / "app-repo" / "feedback.py",
    # THE GUARDS ARE WATCHERS, NOT WORK (t1153 step 5). memory-guard reaches 55 modules through the
    # imports inside its breaker, so nearly every commit to scripts/ left it stale and session_end's
    # stale-services check red. It holds nothing in flight; a restart is a second without a check.
    "memory-guard.service":  REPO / "scripts" / "memory_guard.py",
    "thinkpad-guard.service": REPO / "scripts" / "thinkpad_guard.py",
}


def hold_restart(unit, available=None):
    """Why `unit` must not be restarted right now, or ''. memory-guard trips once per dip and re-arms
    only 2 GB above its reserve; a fresh process is armed, so restarting it mid-dip would trip again."""
    if unit != "memory-guard.service":
        return ""
    sys.path.insert(0, str(REPO / "scripts"))
    import memory_guard
    avail = memory_guard.available_gb() if available is None else available
    if avail < memory_guard.RESERVE_GB + 2:
        return f"{avail:.1f} GB available -- inside a dip the guard has already acted on"
    return ""


# Where a service's own imports are found. A module outside these (the standard library, pip)
# is not the repository's code and is never a reason to restart.
ROOTS = (REPO / "scripts", REPO / "projects" / "local-inference", REPO / "projects" / "app-repo")


def _parse_or_none(f):
    """The file's syntax tree, or None when it does not parse (said on stderr, not swallowed)."""
    import ast
    try:
        return ast.parse(f.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as e:
        print(f"  NOTE: {f} does not parse ({e.msg}, line {e.lineno}); its imports are not followed",
              file=sys.stderr)
        return None


def code_files(src, roots=None, cap=400):
    """src plus every repository module it imports, transitively -- including imports made INSIDE
    functions, which is how the Hub loads flow_layout, roles_pages and the rest.

    WHY (2026-09-30): this compared only the service's main script. The Flow tab's services row
    (t475) changed flow_layout.py alone and never went live: feedback.py was older than the
    running Hub, so "nothing stale" was reported for an hour while it served the old chart."""
    import ast
    roots = [Path(r) for r in (roots or ROOTS)]
    seen, todo = [], [Path(src)]
    while todo and len(seen) < cap:
        f = todo.pop()
        if f in seen or not f.is_file():
            continue
        seen.append(f)
        tree = _parse_or_none(f)
        if tree is None:
            continue                          # a file mid-edit: its own mtime still counts
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names.add(node.module)
                names |= {f"{node.module}.{a.name}" for a in node.names}
        for name in names:
            parts = name.split(".")
            for base in [f.parent, *roots]:
                p = base.joinpath(*parts)
                for cand in (p.with_suffix(".py"), p / "__init__.py"):
                    if cand.is_file() and cand not in seen:
                        todo.append(cand)
    return seen


def newest(src, roots=None):
    """(mtime, path) of the most recently changed file in src's code."""
    return max(((f.stat().st_mtime, f) for f in code_files(src, roots)), default=(0, Path(src)))


def started_at(unit):
    r = subprocess.run(["systemctl", "--user", "show", unit,
                        "-p", "ActiveEnterTimestampMonotonic", "--value"],
                       capture_output=True, text=True, timeout=30)
    try:
        usec = int((r.stdout or "0").strip())
    except ValueError:
        return None
    if not usec:
        return None
    up = float(Path("/proc/uptime").read_text().split()[0])
    return time.time() - (up - usec / 1e6)


def _proc_table():
    """[pid, comm, args] for every process, read from /proc. One that exits mid-scan is gone,
    which is the normal case for a scan, and is left out."""
    rows = []
    for d in Path("/proc").glob("[0-9]*"):
        if not (d / "comm").exists():
            continue
        try:
            comm = (d / "comm").read_text().strip()
            args = (d / "cmdline").read_bytes().decode("utf-8", "replace").replace("\0", " ").strip()
        except OSError as e:
            if d.exists():
                print(f"  NOTE: {d} unreadable ({e}) -- not considered", file=sys.stderr)
            continue
        if args:
            rows.append([d.name, comm, args])
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    acted, skipped = [], []

    for unit, src in SAFE.items():
        if subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit], timeout=30).returncode:
            continue                              # not running; nothing to be stale
        t0 = started_at(unit)
        if t0 is None or not src.is_file():
            continue
        mtime, changed = newest(src)
        if mtime <= t0:
            continue                              # all its code is older than the process: fine
        age = (mtime - t0) / 60
        held = hold_restart(unit)
        if held:
            skipped.append(f"{unit}: stale, NOT restarted -- {held}")
            continue
        if a.dry_run:
            acted.append((unit, f"would restart -- {changed.name} changed {age:.0f} min after start"))
            continue
        r = subprocess.run(["systemctl", "--user", "restart", unit], capture_output=True, timeout=120)
        acted.append((unit, "restarted" if r.returncode == 0 else
                      f"RESTART FAILED: {(r.stderr or b'').decode()[:120]}"))

    # Bare processes are named, never touched. A running coder.py holds a build in flight.
    # /proc, not `ps -eo ...args`: without a terminal ps cuts lines at 80 columns, and a long
    # interpreter path pushes the script name off the end (the ps-command-column check).
    seen = set()
    for parts in _proc_table():
        if "python" not in parts[1]:
            continue                          # only the interpreter, not its shell wrappers
        for name in ("coder.py", "probe.py"):
            if name in parts[2] and "restart_stale" not in parts[2] and name not in seen:
                seen.add(name)
                pid = parts[0]
                f = next(REPO.rglob(name), None)
                if f and f.stat().st_mtime > time.time() - 86400:
                    skipped.append(f"{name} (pid {pid}): {f.name} changed recently. NOT "
                                   f"restarted -- it holds work in flight. Restart it "
                                   f"yourself when its current pass is a good place to stop.")
                break

    for unit, what in acted:
        print(f"  {unit}: {what}")
    for s in skipped:
        print(f"  [left alone] {s}")
    if not acted and not skipped:
        print("  nothing stale")
    if acted and not a.dry_run:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H%M%SZ", time.gmtime()),
                                "acted": acted}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
