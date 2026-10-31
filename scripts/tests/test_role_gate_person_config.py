#!/usr/bin/env python3
"""role_gate: a person may sign a direct commit to main of EXACTLY the role store's files -- the Hub's
Models-tab autocommit (Jas, 2026-10-08) -- and nothing more. Hermetic: git and the store are stubbed."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import role_gate as G  # noqa: E402

FAILED = 0


def check(label, got, want):
    global FAILED
    FAILED += got != want
    print(f"  [{'ok' if got == want else 'BAD'}] {label}: {got!r}")


G.load_roles = lambda path=None: {
    "jas": {"kind": "person", "tasks": [{"task": "assigned_jas"}], "trailers": ["human/jas"]},
    "claude": {"kind": "person", "tasks": [{"task": "direct_commit"}], "trailers": ["claude-code"]}}
G.on_main = lambda: True
G.task_for_merge = lambda: None
msg = Path(tempfile.mkdtemp()) / "m"
msg.write_text("chore(local-inference): Models tab -- lanes 1 -> 2\n\nAgent: human/jas\n")
staged = {"v": ""}
real_git = G.git
G.git = lambda *a: (0, staged["v"]) if a[:2] == ("diff", "--cached") else real_git(*a)

staged["v"] = "\n".join(G.CONFIG_FILES)
check("the three role-store files, signed by Jas: allowed", G.check_commit(str(msg)), 0)
staged["v"] = G.CONFIG_FILES[0]
check("one of them: allowed", G.check_commit(str(msg)), 0)
staged["v"] = G.CONFIG_FILES[0] + "\nAGENTS.md"
check("plus any other file: refused", G.check_commit(str(msg)), 1)
staged["v"] = "scripts/role_gate.py"
check("any other file alone: refused", G.check_commit(str(msg)), 1)
sys.exit(1 if FAILED else 0)
