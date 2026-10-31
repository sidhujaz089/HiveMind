#!/usr/bin/env python3
"""bridge_trigger.resolve_additive (2026-10-08): two scopes that each add an oracle to the failure
ceiling conflict; the freeze keeps both sides' entries instead of waiting on Claude. A conflict in any
other file still refuses. Hermetic: a throwaway git repo, no hooks."""
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


r = Path(tempfile.mkdtemp())
git(r, "init", "-q", "-b", "main")
git(r, "config", "user.email", "t@t"); git(r, "config", "user.name", "t")
ceil = r / "workspace" / "test-failures-ceiling.json"
ceil.parent.mkdir(parents=True)
(r / "other.txt").write_text("a\n")
ceil.write_text(json.dumps({"allowed_to_fail": ["a.py"], "why": {"a.py": "x"}}, indent=1) + "\n")
git(r, "add", "-A"); git(r, "commit", "-qm", "base")
for br, name in (("b1", "b.py"), ("b2", "c.py")):
    git(r, "checkout", "-qb", br, "main")
    d = json.loads(ceil.read_text()); d["allowed_to_fail"].append(name); d["why"][name] = br
    ceil.write_text(json.dumps(d, indent=1) + "\n"); git(r, "commit", "-qam", br)
git(r, "checkout", "-q", "main"); git(r, "merge", "-q", "--no-ff", "-m", "m1", "b1")
m = git(r, "merge", "--no-ff", "-m", "m2", "b2")
check("two scopes adding to the ceiling conflict", m.returncode != 0, True)
ok, why = B.resolve_additive(r, "b2")
check("resolved by keeping both", ok, True)
check("both entries kept, nothing lost", json.loads(ceil.read_text())["allowed_to_fail"], ["a.py", "b.py", "c.py"])
git(r, "commit", "-q", "--no-edit")
git(r, "checkout", "-qb", "b3", "main"); (r / "other.txt").write_text("b3\n"); git(r, "commit", "-qam", "b3")
git(r, "checkout", "-q", "main"); (r / "other.txt").write_text("main\n"); git(r, "commit", "-qam", "m")
git(r, "merge", "--no-ff", "-m", "m3", "b3")
ok, why = B.resolve_additive(r, "b3")
check("a conflict outside the additive lists still refuses", ok, False)
sys.exit(1 if FAILED else 0)
