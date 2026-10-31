#!/usr/bin/env python3
"""coder.py must refuse, INSIDE the attempt loop, what the commit gate would refuse after it.

31 runs ended ACCEPTED-NOT-WRITTEN: the suites went green, the loop accepted, and the
pre-commit ratchet (library.py verify --staged) then refused the commit for a NEW library
finding -- p18 on 2026-09-23 lost a correct fix to `except (OSError, ...): continue`. The
model was never told. These cases pin the fix:

  * library_gate() refuses a patch that ADDS a block finding, and names the check
  * it passes a patch whose finding was already there (the ratchet is relative, not absolute)
  * it passes a clean patch
  * the loop calls it BEFORE run_suites, so a refusal costs no suite run and reaches the model
  * the commit gate and the loop share ONE definition (library_checks.new_blocks)
"""
import inspect
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
sys.path.insert(0, str(ROOT / "scripts"))
import coder  # noqa: E402
import library  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


CLEAN = "import os\n\n\ndef f(paths):\n    for p in paths:\n        os.stat(p)\n"
SWALLOW = ("import os\n\n\ndef f(paths):\n    for p in paths:\n        try:\n"
           "            os.stat(p)\n        except OSError:\n            continue\n")
REL = "scripts/fixture_gate_target.py"


def gate(before, after):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "scripts").mkdir()
        (root / REL).write_text(after)
        return coder.library_gate(root, [{"path": REL}], {REL: before} if before is not None else {})


def main():
    print("library_gate refuses what the commit gate would:")
    ok, msg = gate(CLEAN, SWALLOW)
    report("a patch that ADDS a swallowed exception is refused", ok, False)
    report("  and the message names the check", "swallowed-exception" in msg, True)
    report("  and names the file and line", f"{REL}:" in msg, True)
    ok, _ = gate(None, SWALLOW)
    report("a NEW file carrying the finding is refused", ok, False)
    ok, _ = gate(SWALLOW, SWALLOW + "\n\nX = 1\n")
    report("a finding already present before the patch is NOT refused (ratchet)", ok, True)
    ok, _ = gate(CLEAN, CLEAN + "\n\nX = 1\n")
    report("a clean patch passes", ok, True)

    print("\nIt runs inside the loop, before the suites:")
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    loop_gate = src.find("library_gate(root, obj[\"ops\"]")
    suites = src.find("failed, out = run_suites(root, declared=already)")
    report("the loop calls library_gate", loop_gate > 0, True)
    report("  before run_suites", 0 < loop_gate < suites, True)
    report("  and records its own outcome", "\"LIBRARY-BLOCK\"" in src, True)

    print("\nOne definition, two callers:")
    report("the commit ratchet uses library_checks.new_blocks",
           "library_checks.new_blocks(" in inspect.getsource(library._ratchet), True)
    report("the loop uses library_checks.new_blocks",
           "library_checks.new_blocks(" in inspect.getsource(coder.library_gate), True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
