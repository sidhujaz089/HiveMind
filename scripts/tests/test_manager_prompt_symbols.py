#!/usr/bin/env python3
"""t460 oracle: the manager prompt must state the SYMBOLS rule UNAMBIGUOUSLY -- a SYMBOLS
entry is a bare function or constant name (no '@LINE' suffix, no prose), a new one is written
'<name> (new)' exactly, and line numbers / neighbour anchors go in the single_change and
what_is_wrong prose fields. Today's PREAMBLE says 'mark a new one "(new)"' and 'a :LINE you
cite is a real line of that file' but never says a SYMBOLS entry must be a bare name with no
@LINE and no extra annotation, which is the ambiguity the Q4 model filled by writing
'note_failure_on_task@940' and 'PARKED_MARK (new, anchor ...)' (t324).

Fail-first against today's scripts/manager_prompt.py: the sentinel phrase 'A SYMBOLS entry
is a bare function or constant name' is not in PREAMBLE, so the report FAILs and the suite
exits non-zero. This is the only mechanical check possible for a prompt (prose consumed by a
model); the oracle asserts the rule's text is present, not that a model will comply.
House style: report(name, got, want); run directly with python3 (done_when: exits 0).
No live state, no network, no model calls, no subprocess.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts")]
import manager_prompt as mp  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    preamble = mp.PREAMBLE
    sentinel = "A SYMBOLS entry is a bare function or constant name"
    report("PREAMBLE states the unambiguous bare-name SYMBOLS rule",
           sentinel in preamble, True)
    # the rule must also direct line numbers / anchors to the prose fields
    report("PREAMBLE directs line numbers and neighbour anchors to the prose fields",
           "single_change and what_is_wrong prose" in preamble, True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
