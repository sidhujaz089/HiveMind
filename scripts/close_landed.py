#!/usr/bin/env python3
"""Close a task when its fix has landed: its code merged AND its own tests green on main.

    python3 scripts/close_landed.py            # close what has landed, say what has not
    python3 scripts/close_landed.py --dry-run  # say what would close, change nothing

WHY (Jas, 2026-10-31, after the harness vetting): "did you fix why merged fix doesn't close the task?" Nothing did.
The only automatic close was manager_note.apply, and it fires only when the SCOPER answers "already done". A merge
records a reviewq verdict and deletes the branch but never touches the task. Measured that day: 27 of 182 open harness
tasks had their oracle green on main, and 16 of those had a recorded code merge (scope-quality stage=merge,
outcome=merged), some as old as 2026-10-09. They sat at "doing" until a person read them.

THE RULE, mechanical and the same one the scoper path already uses:
  * a candidate is an OPEN task with a code-merge row in workspace/scope-quality.jsonl;
  * its tests are manager_note.task_tests(): every brief's DONE WHEN, and the task's own;
  * every one must exist on main and exit 0. No tests -> not closed, and said. Red -> not closed, and said;
  * taskstore.set_status refuses a task that still owns a red allowed-to-fail suite, and that refusal stands.

merge_reviewed.move_main starts this, detached, after every merge it lands (the auto-merge gate lands through the same
function), so the merge is the moment a task can close, not the next person who happens to look.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TIMEOUT_S = 600


def merged_tasks(ledger=None):
    """Task ids with a code-merge row: scope-quality stage=merge, outcome=merged."""
    p = Path(ledger or (REPO / "workspace" / "scope-quality.jsonl"))
    out = set()
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        print(f"close_landed: {p} unreadable ({type(e).__name__}); nothing judged", file=sys.stderr)
        return out
    bad = 0
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            r = {}
            bad += bool(ln.strip())
        if r.get("stage") == "merge" and r.get("outcome") == "merged" and r.get("task"):
            out.add(r["task"])
    if bad:
        print(f"close_landed: {bad} unreadable row(s) in {p} were not judged", file=sys.stderr)
    return out


def open_task(tid, tasks_dir=None):
    """(path, body) of an OPEN task file, else (None, None)."""
    d = Path(tasks_dir or (REPO / "workspace" / "tasks"))
    for p in sorted(d.glob(f"{tid}--*.md")):
        body = p.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"^status:\s*(\S+)", body, re.M)
        if m and m.group(1) not in ("done", "cancelled"):
            return p, body
    return None, None


def judge(tid, body, repo=REPO, tests_for=None, run=None):
    """(ok, evidence). ok only when every test that defines done exists and exits 0 on `repo`."""
    if tests_for is None:
        sys.path.insert(0, str(REPO / "scripts"))
        import manager_note
        tests_for = manager_note.task_tests
    tests = tests_for(tid, body)
    if not tests:
        return False, "no test defines done (no DONE WHEN in its briefs or body)"
    missing = [t for t in tests if not (Path(repo) / t).is_file()]
    if missing:
        return False, f"its test(s) are not on main: {', '.join(missing)}"
    run = run or (lambda t: subprocess.run([sys.executable, t], cwd=str(repo), capture_output=True,
                                           timeout=TIMEOUT_S).returncode)
    red = []
    for t in tests:
        try:
            rc = run(t)
        except subprocess.TimeoutExpired:
            rc = "timeout"
        if rc != 0:
            red.append(f"{t} ({rc})")
    if red:
        return False, f"merged, but its test(s) are not green on main: {', '.join(red)}"
    return True, "merged, and its test(s) pass on main: " + ", ".join(tests)


def sweep(dry_run=False, ledger=None, tasks_dir=None, repo=REPO, tests_for=None, run=None, store=None):
    """[(tid, closed, evidence)] for every open task with a code merge."""
    results = []
    for tid in sorted(merged_tasks(ledger)):
        path, body = open_task(tid, tasks_dir)
        if not path:
            continue
        ok, evidence = judge(tid, body, repo, tests_for, run)
        closed = False
        if ok and not dry_run:
            if store is None:
                sys.path.insert(0, str(REPO / "projects" / "app-repo"))
                import taskstore as store
            store.add_note(tid, f"DONE: {evidence}. Closed by close_landed.py when the merge landed.",
                           who="close-landed")
            closed = store.set_status(tid, "done") is not False
            if not closed:
                evidence += " -- but taskstore refused the close (see its message)"
        results.append((tid, closed, evidence))
        print(f"  {tid}: {'CLOSED' if closed else ('would close' if ok and dry_run else 'left open')} -- {evidence}")
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="say what would close, change nothing")
    a = ap.parse_args(argv)
    r = sweep(dry_run=a.dry_run)
    print(f"close_landed: {sum(c for _, c, _ in r)} closed, {len(r) - sum(c for _, c, _ in r)} left open "
          f"of {len(r)} open task(s) with a code merge")
    return 0


if __name__ == "__main__":
    sys.exit(main())
