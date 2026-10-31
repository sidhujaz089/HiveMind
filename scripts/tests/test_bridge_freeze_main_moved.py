#!/usr/bin/env python3
"""bridge_trigger.freeze_branch must refuse when main moves during its conflict resolution
(2026-10-19). bf23faf2a: a fast-forward of main landed 12 s into the freeze's additive resolve, and the
freeze's commit took the new HEAD as parent with the index built from the old one -- silently
reverting the fast-forwarded commit. Hermetic: a throwaway repo; the race is simulated by moving
HEAD from inside resolve_additive.

Run: python3 scripts/tests/test_bridge_freeze_main_moved.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import bridge_trigger as B  # noqa: E402

FAILED = 0


def check(label, got, want):
    global FAILED
    FAILED += got != want
    print(f"  [{'ok' if got == want else 'BAD'}] {label}: {got!r}")


def git(r, *a):
    return subprocess.run(["git", "-C", str(r), "-c", "core.hooksPath=/dev/null", *a],
                          capture_output=True, text=True)


def repo_with_conflict():
    r = Path(tempfile.mkdtemp(prefix="freeze-moved-"))
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@t"); git(r, "config", "user.name", "t")
    git(r, "config", "core.hooksPath", "/dev/null")
    ceil = r / "workspace" / "test-failures-ceiling.json"
    ceil.parent.mkdir(parents=True)
    ceil.write_text(json.dumps({"allowed_to_fail": ["a.py"]}, indent=1) + "\n")
    (r / "fix.py").write_text("old\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "base")
    git(r, "checkout", "-qb", "bridge/t1", "main")
    d = json.loads(ceil.read_text()); d["allowed_to_fail"].append("b.py")
    ceil.write_text(json.dumps(d, indent=1) + "\n"); git(r, "commit", "-qam", "bridge")
    git(r, "checkout", "-q", "main")
    d = json.loads(ceil.read_text()); d["allowed_to_fail"].append("c.py")
    ceil.write_text(json.dumps(d, indent=1) + "\n"); git(r, "commit", "-qam", "main adds c")
    git(r, "branch", "late")                                   # a commit that lands mid-freeze
    git(r, "checkout", "-q", "late"); (r / "fix.py").write_text("NEW\n"); git(r, "commit", "-qam", "late fix")
    git(r, "checkout", "-q", "main")
    return r


def run(move):
    r = repo_with_conflict()
    real = B.resolve_additive

    def racing(repo, branch):
        out = real(repo, branch)
        if move:      # what the fast-forward did: HEAD moves, the merge state stays
            late = git(r, "rev-parse", "late").stdout.strip()
            git(r, "update-ref", "refs/heads/main", late)
        return out
    B.resolve_additive = racing
    orig_enabled = B.freeze_enabled
    B.freeze_enabled = lambda *a, **k: (True, "")
    try:
        rc = B.freeze_branch(r, "t1", "test-model", record=False, note=lambda *a, **k: None,
                             requeue=lambda t: None)
    finally:
        B.resolve_additive, B.freeze_enabled = real, orig_enabled
    return r, rc


r, rc = run(move=False)
check("no race: the additive conflict still merges", rc, 0)
r, rc = run(move=True)
check("main moved mid-freeze: refused", rc, 4)
check("  and the late commit's change is still on main", git(r, "show", "main:fix.py").stdout, "NEW\n")
check("  and no freeze commit was made", "freeze" in git(r, "log", "-1", "--format=%s", "main").stdout, False)
sys.exit(1 if FAILED else 0)
