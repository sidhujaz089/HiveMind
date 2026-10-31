#!/usr/bin/env python3
"""check_reassignment_notes refuses a hand-edited reassignment and passes one taskstore made.

Runs in a throwaway git repo under ~/.cache/hivemind-tmp; the real tasks are never touched."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check_reassignment_notes as c  # noqa: E402

failed = 0


def check(name, ok):
    global failed
    failed += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")


base = Path.home() / ".cache" / "hivemind-tmp"
base.mkdir(parents=True, exist_ok=True)
d = Path(tempfile.mkdtemp(dir=str(base)))
saved = c.REPO
try:
    def run(*a):
        subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, check=True)
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    f = d / "workspace" / "tasks" / "t9001--x.md"
    f.parent.mkdir(parents=True)
    f.write_text("---\nid: t9001\nassigned: production\n---\n\n## Notes\n")
    run("add", "-A")
    run("commit", "-q", "-m", "seed", "--no-verify")
    c.REPO = d

    f.write_text(f.read_text().replace("assigned: production", "assigned: claude"))
    run("add", "-A")
    bad = c.violations()
    check("a hand edit of `assigned:` with no note is refused", [b[1:] for b in bad] == [("production", "claude")])

    f.write_text(f.read_text() + "\n**2026-10-19 · taskstore** — Reassigned production -> claude by x: y\n")
    run("add", "-A")
    check("the same change with its Reassigned note passes", c.violations() == [])

    f.write_text(f.read_text().replace("assigned: claude", "assigned: jas"))
    run("add", "-A")
    check("a note for a DIFFERENT change does not cover this one", len(c.violations()) == 1)

    f.write_text(f.read_text() + "\n**2026-10-19 · taskstore** — Reassigned claude -> production by x: y\n"
                 "\n**2026-10-19 · taskstore** — Reassigned production -> jas by z: w\n")
    run("add", "-A")
    check("two moves between commits, each with its note, chain from old to new", c.violations() == [])

    run("reset", "-q", "--hard")
    f.write_text(f.read_text() + "\nanother note\n")
    run("add", "-A")
    check("a change that does not touch the assignee passes", c.violations() == [])
finally:
    c.REPO = saved
    shutil.rmtree(d, ignore_errors=True)
print("FAILED" if failed else "ok")
sys.exit(1 if failed else 0)
