#!/usr/bin/env python3
"""t326 oracle: a SIGKILL during test_enforcement_ratchet must leave no fixture bytes
in any tracked file.

The incident (2026-09-15): the ratchet suite wrote an L-998 fixture into the real
docs/LESSONS.md and restored it in a finally block. The kernel OOM-killed the
process between the two; the fixture survived; check_enforcement failed against the
real repo and coder.py refused every dispatch on an untrustworthy baseline. A
finally block cannot survive a SIGKILL -- the only durable fix is that the fixture
never lands in a tracked file at all.

This oracle reproduces the incident deterministically:
  1. snapshot the exact bytes of docs/LESSONS.md and workspace/enforcement-ceiling.json;
  2. in a child process, import the suite, turn its next subprocess.run (the checker
     invocation -- the very next action after the real files are written) into a
     SIGKILL, and call its append step run_against();
  3. the child dies inside the mutation window -- the 2026-09-15 kill point, made
     deterministic -- and this parent asserts the tracked files are byte-identical.

The oracle also pins the contract the fix must keep: run_against(lessons_text=...,
ceiling_obj=...) with that name and signature, the checker launched via
subprocess.run, and an import-safe suite (no module-level side effects).

A red run has, by design, provoked a leak in the real tree; before it fails it
restores the exact snapshot bytes, so the red run proves the incident without
re-enacting it for the next session.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUITE = REPO / "scripts" / "tests" / "test_enforcement_ratchet.py"
LESSONS = REPO / "docs" / "LESSONS.md"
CEILING = REPO / "workspace" / "enforcement-ceiling.json"
ORPHAN = REPO / "scripts" / "tests" / "test_nothing_invokes_this.py"
MARKER = "IN-MUTATION-WINDOW"

# The child: enter the suite's mutation step and let the checker invocation kill the
# process with SIGKILL. That is the 2026-09-15 kill point, made deterministic.
CHILD = (
    "import os, signal, subprocess, sys\n"
    "sys.path.insert(0, {suite_dir!r})\n"
    "import test_enforcement_ratchet as t\n"
    "def bomb(*a, **k):\n"
    "    print('IN-MUTATION-WINDOW', file=sys.stderr, flush=True)\n"
    "    os.kill(os.getpid(), signal.SIGKILL)\n"
    "subprocess.run = bomb\n"
    "fixture = 'L-998 [VERIFIED 2026-09-03] leak-oracle fixture.\\n\\n"
    "ENFORCEMENT: scripts/this_was_never_written.py runs every session.\\n'\n"
    "t.run_against(lessons_text=fixture, ceiling_obj={{'max_unenforced': 0}})\n"
    "print('SURVIVED-UNEXPECTED', file=sys.stderr, flush=True)\n"
).format(suite_dir=str(SUITE.parent))


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    if not SUITE.exists():
        print("FAIL: the suite under test is missing: %s" % SUITE)
        return 1
    try:
        l0, c0 = LESSONS.read_text(), CEILING.read_text()
    except OSError as e:
        print("FAIL: cannot snapshot the live files before the kill test: %r" % (e,))
        return 1
    orphan0 = ORPHAN.exists()
    s0l, s0c = _sha256(LESSONS), _sha256(CEILING)

    try:
        child = subprocess.run([sys.executable, "-c", CHILD],
                               capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired as e:
        tail = (e.stderr or "")[-400:]  # tail only, for the FAIL line
        print("FAIL: the child neither died in the window nor finished (120 s timeout); "
              "the oracle cannot place its kill. stderr tail: %s" % tail)
        return 1

    leak = []
    l1, c1 = LESSONS.read_text(), CEILING.read_text()
    orphan1 = ORPHAN.exists()
    s1l, s1c = _sha256(LESSONS), _sha256(CEILING)

    if MARKER not in (child.stderr or ""):
        tail = (child.stderr or "")[-300:]  # tail only, for the FAIL line
        leak.append("the kill did not land inside the mutation step (no %s marker); "
                    "stderr tail: %s" % (MARKER, tail))
    if child.returncode != -9:
        leak.append("the child was not SIGKILLed in the window (rc=%r); the kill did not "
                    "happen where the kernel's did" % (child.returncode,))
    if l1 != l0:
        leak.append("docs/LESSONS.md carries fixture bytes after the kill "
                    "(sha256 %s -> %s) -- the 2026-09-15 incident, reproduced" % (s0l, s1l))
    if c1 != c0:
        leak.append("workspace/enforcement-ceiling.json carries fixture bytes after the "
                    "kill (sha256 %s -> %s)" % (s0c, s1c))
    if orphan1 and not orphan0:
        leak.append("scripts/tests/test_nothing_invokes_this.py appeared after the kill")

    if leak:
        # A red run provoked a real leak. Restore the exact snapshot bytes before failing,
        # so this oracle never re-enacts the incident it is proving.
        try:
            LESSONS.write_text(l0)
            CEILING.write_text(c0)
            if orphan1 and not orphan0:
                ORPHAN.unlink()
        except OSError as e:
            print("FAIL: could not restore the snapshot bytes: %r -- run: "
                  "git checkout -- docs/LESSONS.md workspace/enforcement-ceiling.json" % (e,))
            leak.append("restoration FAILED -- the tree still carries the fixture")
        else:
            if (LESSONS.read_text() == l0 and CEILING.read_text() == c0
                    and not ORPHAN.exists()):
                print("  (the red run leaked fixture bytes into the real tree; the exact "
                      "snapshot bytes were restored and re-verified)")
            else:
                leak.append("restoration FAILED -- the tree still carries the fixture; "
                            "run: git checkout -- docs/LESSONS.md "
                            "workspace/enforcement-ceiling.json")

    for x in leak:
        print("FAIL:", x)
    if leak:
        print("test_enforcement_ratchet_no_leak: FAILED")
        print("  A test must not mutate a tracked file in place: test_enforcement_ratchet "
              "must build its fixtures in a temp sandbox (with a runtime copy of "
              "check_enforcement.py, which roots off its own __file__) so a SIGKILL can "
              "leave nothing but an abandoned temp dir.")
        return 1

    print("  [ok]    SIGKILL inside the mutation window left no fixture bytes in tracked "
          "files (rc=%d, %s marker seen, lessons sha256 %s, ceiling sha256 %s)" %
          (child.returncode, MARKER, s0l[:12], s0c[:12]))
    print("test_enforcement_ratchet_no_leak: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
