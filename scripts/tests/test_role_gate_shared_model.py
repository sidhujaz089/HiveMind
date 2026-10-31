#!/usr/bin/env python3
"""role_gate: a bridge freeze signed `ollama/<model>` passes when ANY role on that model has Scoping
(2026-10-08: every role ran swift, role_for picked the Manager, which lacked Scoping, and every
automatic freeze was refused for 18 hours). A coder/* merge still needs Merge review.
Hermetic: git and the role store are stubbed; no repository is touched."""
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


ROLES = {"manager": {"kind": "model", "model": "swift", "tasks": [{"task": "rca"}]},
         "senior": {"kind": "model", "model": "swift", "tasks": [{"task": "scoping"}]}}
G.load_roles = lambda path=None: ROLES
G.on_main = lambda: True
msg = Path(tempfile.mkdtemp()) / "m"
msg.write_text("bridge t1 -- frozen\n\nAgent: ollama/swift\n")

G.task_for_merge = lambda: "scoping"
check("Manager listed first lacks Scoping, Senior on the same model has it: freeze passes",
      G.check_commit(str(msg)), 0)
G.task_for_merge = lambda: G.MERGE_TASK
check("a coder/* merge signed ollama/swift still refused (no role has Merge review)",
      G.check_commit(str(msg)), 1)
ROLES["senior"]["tasks"] = []
G.task_for_merge = lambda: "scoping"
check("no role on swift has Scoping: freeze refused", G.check_commit(str(msg)), 1)
sys.exit(1 if FAILED else 0)
