#!/usr/bin/env python3
"""Refuse a commit that reassigns a task without the note saying who and why (Jas, 2026-10-19).

    python3 scripts/check_reassignment_notes.py            # check what is staged; exit 1 if any
    python3 scripts/check_reassignment_notes.py --worktree # the same over uncommitted changes

taskstore.set_meta writes "Reassigned <old> -> <new> by <code>: <why>" on every change of assignee
(aefdad4b). That covers every caller of set_meta -- and not a hand edit of the file: on 2026-10-17
a Claude session moved t1309 back to production with `sed -i 's/^assigned: claude$/.../'`, which
set_meta never saw. This closes that gap at the one point every change passes through: the commit.
A task file whose `assigned:` changed must gain, in the same diff, a note naming that exact change.
New task files are not checked (taskstore.add files them with their first assignee).
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TASK_FILE = re.compile(r"^workspace/tasks/(?:done/)?t\d+--.*\.md$")


def git(*a):
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, timeout=60)


def assignee(text):
    m = re.search(r"^assigned:[ \t]*(\S*)", text or "", re.M) or re.search(r"^needs:[ \t]*(\S*)", text or "", re.M)
    v = (m.group(1) if m else "").strip()
    return "production" if v in ("", "model", "production") else v


def reaches(added, old, new):
    """True when the added notes' "Reassigned A -> B" steps chain from `old` to `new`. A task can be
    moved more than once between commits (Claude -> production, then the scheduler -> the scoper);
    each move has its own note, and together they account for the change."""
    edges = {}
    for a, b in re.findall(r"Reassigned (\S+) -> (\S+)", added):
        edges.setdefault(a, set()).add(b)
    # A TIER MIGRATION IS NOT A REASSIGNMENT (2026-10-29). taskstore.set_tier() records
    # "needs: <old> -> <new> (tier migration; NOT a reassignment -- nothing was handed to a person or a model)",
    # because moving a task between the vision/technical/rework/production TIERS is not handing it to anybody. This
    # check demanded a "Reassigned" line for any change of the value and so failed on all 49 tasks the tier
    # migration rewrote -- which, because the check runs in the commit hook, blocked EVERY commit on main, not just
    # the migration's. The sanctioned note is accepted here as an edge in the same chain; a real reassignment with
    # no note still fails, which is what this check is for.
    for a, b in re.findall(r"needs:\s*(\S+) -> (\S+) \(tier migration", added):
        edges.setdefault(a, set()).add(b)
    seen, todo = set(), [old]
    while todo:
        x = todo.pop()
        if x == new:
            return True
        if x not in seen:
            seen.add(x)
            todo.extend(edges.get(x, ()))
    return False

def violations(worktree=False):
    """[(path, old, new)] for task files whose assignee changed with no matching note added."""
    diff = git("diff", "--name-status", *([] if worktree else ["--cached"]))
    if diff.returncode != 0:            # a failed diff examined nothing -- never read it as "no violations"
        raise RuntimeError(f"git diff failed ({diff.returncode}): {diff.stderr.strip()}")
    out = []
    for line in diff.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or parts[0] != "M" or not TASK_FILE.match(parts[-1]):
            continue
        path = parts[-1]
        old_text = git("show", f"HEAD:{path}").stdout
        new_text = (REPO / path).read_text(encoding="utf-8", errors="replace") if worktree \
            else git("show", f":{path}").stdout
        old, new = assignee(old_text), assignee(new_text)
        if old == new:
            continue
        added = "\n".join(l[1:] for l in git("diff", *([] if worktree else ["--cached"]), "-U0", "--", path)
                          .stdout.splitlines() if l.startswith("+") and not l.startswith("+++"))
        if not reaches(added, old, new):
            out.append((path, old, new))
    return out


def main(argv):
    bad = violations(worktree="--worktree" in argv)
    for path, old, new in bad:
        print(f"  {path}: assigned {old} -> {new} with no 'Reassigned {old} -> {new}' note in this change")
    if bad:
        print("  Reassign through taskstore (set_meta / `taskstore.py file|hold|release`), which writes the\n"
              "  note -- never by editing the file.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
