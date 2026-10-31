#!/usr/bin/env python3
"""Is the caller a TEST, and is it about to touch LIVE state? One answer, in one place.

    import live_state_guard
    if live_state_guard.in_test():
        live_state_guard.refuse_live_id_mint(space, fix="HIVEMIND_TASKS_DIR")

WHY THIS FILE EXISTS (2026-10-27, L2 Phase 0a). Two near-identical rules were being written by
different lanes in different files: `scheduler_commands.refuse_live_systemctl_from_test` (a suite
ran `systemctl stop` on the live production units at 13:24, 14:22, 14:26, 14:29 and 14:34 MDT on
2026-10-23, and the Manager and Auditor never stayed up) and `refuse_live_device_pause_ledger`
(same shape, the live ledger). The taskstore id counter is the third: suites running in worktrees
minted live task numbers, and workspace/tasks/.ids stood at 9354 against a highest task file of
t8860 -- 494 numbers between them that no task will ever carry.

The DETECTION is the same in all three and it is deliberately weak: a process attests to being a
test by its own argv[0] being under scripts/tests/ or named test_*.py, or by the runner setting
HIVEMIND_IN_TEST. It is an honour system that a test can only break by trying, and it fails closed
for the case that actually happened -- a suite importing the module directly.

REFUSING IS NOT THE WHOLE GUARD: a refusal that is the wrong shape gets `--no-verify`d or deleted.
Each caller passes the FIX, so the message a suite author reads is the change that makes it pass.
"""
import os
import sys
from pathlib import Path


def looks_like_a_test(argv0):
    """True when argv0 names a suite: a path under scripts/tests/, or a file named test_*.py."""
    a = str(argv0 or "").replace(os.sep, "/")
    return "scripts/tests/" in a or Path(a).name.startswith("test_")


def in_test(argv0=None):
    """True when this process says it is a test: test-shaped argv[0], or HIVEMIND_IN_TEST set."""
    if os.environ.get("HIVEMIND_IN_TEST"):
        return True
    return looks_like_a_test(argv0 if argv0 is not None else (sys.argv[0] if sys.argv else ""))


def _raise(what, target, fix):
    raise RuntimeError(
        f"refusing to touch LIVE state from a test: {what}\n"
        f"  {target}\n"
        f"{fix}\n"
        f"  (live_state_guard: this process looks like a suite -- argv[0] or HIVEMIND_IN_TEST. "
        f"Nothing was written.)")


def refuse_live_id_mint(space, fix=None):
    """A suite must not draw a number out of the live task sequence."""
    _raise("reserving a task id", space,
           "  " + (fix or "Point the store at a temp dir before importing taskstore: "
                           "HIVEMIND_TASKS_DIR=/tmp/fixture/tasks. Every run of this suite "
                           "otherwise burns a real id out of workspace/tasks/.ids."))


def refuse_live_write(path, what, fix):
    """A suite must not write the named live file, ledger or unit state."""
    _raise(what, path, "  " + fix)


def same(a, b):
    """Two paths are the same file, resolved. A symlinked spelling of the live path is the live path."""
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return False
