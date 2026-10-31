#!/usr/bin/env python3
"""Fail-first oracle for the t645 deliverable gate in projects/local-inference/probe.py.

The 45-round settings-review run (RCA workspace/rca/19689cd28c8e.md) was stopped mid-flight and
its deliverable was LOST. probe.py's only deliverable-shaped write -- the `with LOG.open("a", ...)`
append -- sits AFTER the round loop `for n in range(1, a.rounds + 1):`, and its `done` branch
breaks on the model's say-so with no file check. A run interrupted between rounds therefore leaves
ZERO artifact: the RCA found the settings-review .md "absent at r17 and r30".

RED on main because the two functions the fix adds do not exist in probe.py:

    probe.write_deliverable(path, body)   -- incremental writer: a partial file after round 1
    probe.deliverable_gate(path)          -- (ok, why) fail-closed: absent / 0-byte / dir / stable

Both are pure -- a path and a string; no Ollama, no env3, no live server, no subprocess, no
urlopen, no fixed /tmp (tempfile only), and no process left running.

Run it on main and read WHICH assertions fail: the first two must be the "exists" checks.
"""
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
# probe.py imports jsonx, settings and loudcap at module level BEFORE its own sys.path inserts.
# Add both candidate directories first, so an import failure means something real about probe.py
# and never about a path this oracle forgot. (parents[2] from scripts/tests/ = repo root.)
for _d in (REPO / "projects" / "local-inference", REPO / "scripts"):
    _p = str(_d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import probe
except Exception as e:
    # A DISTINCT exit code (2) and a distinct line, so an oracle bug is never read as the
    # expected red (exit 1: the two functions are missing).
    print("ORACLE BUG (import failed, NOT the expected red): %s: %s" % (type(e).__name__, e))
    sys.exit(2)

_failures = []


def check(label, cond, detail=""):
    line = "  [%s] %s" % ("ok" if cond else "BAD", label)
    if not cond and detail:
        line += " -- " + detail
    print(line)
    if not cond:
        _failures.append(label)


def main():
    print("t645 deliverable-gate oracle (probe.py)\n")

    have_write = hasattr(probe, "write_deliverable")
    have_gate = hasattr(probe, "deliverable_gate")
    check("probe.write_deliverable exists", have_write,
          "the incremental writer is not implemented")
    check("probe.deliverable_gate exists", have_gate,
          "the fail-closed gate is not implemented")
    if not (have_write and have_gate):
        print("\n%d assertion(s) failed -- the deliverable gate is not in probe.py yet"
              % len(_failures))
        return 1

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)

        # write_deliverable: parent dirs created, and EXACTLY `body` on disk (assert the VALUE,
        # not that a substring appears -- a prefix write passes a substring check).
        target = td / "nested" / "dir" / "review.md"
        body = "round 1: initial partial review\n"
        probe.write_deliverable(str(target), body)
        check("write_deliverable creates the nested parent dirs and the file", target.is_file())
        check("write_deliverable writes EXACTLY the bytes given (not a prefix, not a repr)",
              target.is_file() and target.read_text(encoding="utf-8") == body,
              "got %r" % (target.read_text(encoding="utf-8") if target.is_file() else None))

        # Path (not just str) is accepted -- the harness will hand it a Path if a caller builds one.
        via_path = td / "as-path.md"
        probe.write_deliverable(via_path, "path object accepted\n")
        check("write_deliverable accepts a Path as well as a str",
              via_path.is_file() and via_path.read_text(encoding="utf-8") == "path object accepted\n")

        # THE INTERRUPTION PROPERTY -- the whole reason this module exists: a later round leaves a
        # NON-EMPTY artifact on disk. Asserted as "exists and size>0", the requirement ("a partial
        # file beats a perfect one"), agnostic to append-vs-overwrite so a correct build is not
        # pinned to one of them.
        probe.write_deliverable(str(target), "round 2: appended\n")
        check("a second round leaves a NON-EMPTY artifact on disk (interruption property)",
              target.is_file() and target.stat().st_size > 0)
        check("  ...and a later round's content is readable as text (not truncated mid-write)",
              target.is_file() and "\x00" not in target.read_text(encoding="utf-8"))

        # deliverable_gate: FOUR distinct real inputs, each a separate real state.
        absent = td / "never-written.md"
        ok, why = probe.deliverable_gate(str(absent))
        check("gate FAILS when the deliverable is ABSENT", ok is False, "returned %r" % (ok,))
        check("  ...and says WHY (a gate that fails silently is blind)", bool(str(why).strip()))

        empty = td / "empty.md"
        empty.write_text("", encoding="utf-8")
        ok, why = probe.deliverable_gate(str(empty))
        check("gate FAILS when the deliverable is EMPTY (0 bytes)", ok is False,
              "returned %r -- a 0-byte file is the interruption signature" % (ok,))
        check("  ...and says WHY", bool(str(why).strip()))

        ok, why = probe.deliverable_gate(str(td))
        check("gate FAILS when the path is a DIRECTORY, not a file", ok is False,
              "returned %r -- an exists() check alone passes a directory" % (ok,))
        check("  ...and says WHY", bool(str(why).strip()))

        ok, why = probe.deliverable_gate(str(target))
        check("gate PASSES when the deliverable exists, is non-empty and byte-stable",
              ok is True, "returned %r -- why=%s" % (ok, why))

        # A gate that returns True for everything satisfies the checks above only if it also fails
        # these. This is the anti-stub negative: a `return True` implementation cannot pass.
        probe.write_deliverable(str(target), "round 3: appended again\n")
        probe.deliverable_gate(str(target))          # warm any cache the implementation keeps
        ok, why = probe.deliverable_gate(str(absent))
        check("gate STILL fails for a path that was never written, after other writes",
              ok is False, "returned %r -- state leaking across paths" % (ok,))

    n = len(_failures)
    print("\n%d failed -- %s" % (n, "the deliverable gate works" if not n
                                else "the deliverable gate is incomplete"))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
