#!/usr/bin/env python3
"""Every enrolled build chunk belongs to a task: its brief's first TASK: line names a task on disk.

Jas, 2026-09-25: "everything should have a task associated with it." 28 enrolled chunks had no
TASK line -- hand-written enrolments (c9f7f8c, d7cdebf, 15b08ae) went straight into
scripts/autonomy_loop.sh, only compliance chunks minted after a7b7e2d got one, and neither
coder.py nor dispatch_loop required it. The only exception is a chunk declared in
workspace/briefs/task-links-pending.json (a delivered branch awaits review, so its brief cannot
change yet) -- and only until its 'until' date. Run at pre-commit when the table or a brief changes.
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOOP = ROOT / "scripts" / "autonomy_loop.sh"
BRIEFS = ROOT / "workspace" / "briefs"
TASKS = ROOT / "workspace" / "tasks"
PENDING = BRIEFS / "task-links-pending.json"


def task_dirs():
    """This checkout's task store, plus the MAIN checkout's when this is a linked worktree.

    THE BRIDGE COMMITS FROM A WORKTREE CUT FROM HEAD (2026-10-02). A task the routing created after
    the last commit exists only in the main checkout's working tree, so here it read as 'names no
    task on disk' and the Manager's t648 scope was refused three times (~40 min of Q4 each)."""
    import subprocess
    dirs = [TASKS]
    try:
        common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-common-dir"],
                                capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError) as e:
        print(f"  NOTE: git unreadable ({e}); checking this checkout's tasks only")
        return dirs
    main_tasks = (ROOT / common).resolve().parent / "workspace" / "tasks" if common else None
    if main_tasks and main_tasks.resolve() != TASKS.resolve() and main_tasks.is_dir():
        dirs.append(main_tasks)
    return dirs


def resolve_task_id(text, tasks):
    """The task id a brief's first TASK: line names, resolved to a task ON DISK.

    A brief may name a PHASE of a task ("TASK: t1169a (phase 1 of 2: helpers only, ...").
    The guarantee this file enforces is that an enrolled chunk belongs to a task that exists
    on disk, so a suffixed id is accepted ONLY when its BASE id (t1169) is a task on disk. A
    suffix over a base that is not on disk names no task, exactly as a bare unknown id does.
    Returns (display_id, resolved_id); resolved_id is None when nothing resolves, and
    display_id is None when the text has no TASK: line at all.
    """
    m = re.search(r"^\s*TASK:\s*(t\d+)([a-z0-9]*)\b", text, re.M)
    if not m:
        return None, None
    full = m.group(1) + m.group(2)
    if full in tasks:
        return full, full
    if m.group(2) and m.group(1) in tasks:
        return full, m.group(1)
    return full, None


def check_resolution():
    """Pins the phase-suffix rule. Pure: reads no file, writes nothing, runs no process."""
    fails = []
    T = {"t1169", "t798"}

    def expect(text, tasks, want_display, want_resolved, label):
        got = resolve_task_id(text, tasks)
        if got != (want_display, want_resolved):
            fails.append(f"{label}: resolve_task_id returned {got!r}, "
                         f"expected {(want_display, want_resolved)!r}")

    expect("TASK: t1169a (phase 1 of 2: helpers only, do NOT touch launch())", T,
           "t1169a", "t1169", "phase id whose base is on disk")
    expect("TASK: t798", T, "t798", "t798", "bare id, no suffix")
    expect("TASK: t798 (step 1 of 2: parse and baseline)", T, "t798", "t798",
           "bare id with a parenthetical")
    expect("TASK: t798x", T | {"t798x"}, "t798x", "t798x",
           "suffix that is itself a task on disk (exact match wins)")
    expect("TASK: t9999z (phase 1)", T, "t9999z", None,
           "suffix whose base is NOT on disk stays a finding")
    expect("TASK: t11699a", {"t1169"}, "t11699a", None,
           "reject prefix matching: t1169 is a prefix of t11699a, not its base")
    expect("see t1169 for context\nBODY", T, None, None,
           "a task id mentioned in prose is not a TASK: line")
    expect("TASK: T1169", T, None, None, "uppercase id: unchanged, no match today")
    expect("TASK: t1169A", T, None, None, "uppercase suffix: unchanged, no match today")
    expect("", T, None, None, "empty brief text keeps 'brief names no task'")
    return fails


def main():
    tasks = {f.name.split("--")[0] for d in task_dirs()
             for f in list(d.glob("t*.md")) + list((d / "done").glob("t*.md"))}
    pend = json.loads(PENDING.read_text())["pending"] if PENDING.is_file() else {}
    today = time.strftime("%Y-%m-%d")
    bad = []
    for why in check_resolution():
        bad.append(f"resolution check: {why}")
    for line in LOOP.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(p\d+-[a-z0-9-]+)\|([^|]+)\|", line)
        if not m:
            continue
        chunk, brief = m.groups()
        f = BRIEFS / brief
        text = f.read_text(encoding="utf-8", errors="replace") if f.is_file() else ""
        full, tid = resolve_task_id(text, tasks)
        if tid:
            continue
        p = pend.get(chunk)
        if p and p.get("until", "") >= today and p.get("task") in tasks:
            continue
        if full:
            why = f"TASK {full} names no task on disk"
        elif p and p.get("until", "") < today:
            why = f"pending link EXPIRED {p['until']}"
        elif p:
            why = f"pending link names {p.get('task')}, which is not a task on disk"
        else:
            why = "brief names no task"
        bad.append(f"{chunk} ({brief}): {why}")
    for b in bad:
        print(f"  FAIL  {b}")
    print(f"\n{0 if bad else 1} passed, {len(bad)} failed"
          + ("" if bad else "  -- every enrolled chunk belongs to a task"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
