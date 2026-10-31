#!/usr/bin/env python3
"""The preflight shows the reviewer the repo files the WORK ORDER names (Jas, 2026-09-29: six of 15
held briefs were objections to files the reviewer was never shown, all of which exist)."""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / "scripts")]
import preflight_brief as pb  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


r = pb.brief_refs("Build the body with settings.request(section, model, messages).")
report("module.function( names the module (p34, p53)", "projects/local-inference/settings.py" in r, True)
r = pb.brief_refs("lazy-import scheduler_commands as sched and dispatch_loop.ledger_running")
report("a snake_case module.attr names the module (p170)",
       ("scripts/dispatch_loop.py" in r, "scripts/scheduler_commands.py" in r), (True, True))
r = pb.brief_refs('NEWER = "scripts/check_enforcement.py"')
report("a repo path names the file (p171)", r, ["scripts/check_enforcement.py"])
report("a file that does not exist is NOT shown -- that is the reviewer's to object to",
       pb.brief_refs("see scripts/no_such_file_xyz.py and import no_such_module_xyz"), [])
report("already-shown files are skipped",
       pb.brief_refs("scripts/check_enforcement.py", skip=["scripts/check_enforcement.py"]), [])
out = pb._outline("projects/local-inference/settings.py")
report("a large file is shown as an outline that still names request()",
       ("OUTLINE" in out, "def request(section, model, messages, **override):" in out), (True, True))
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
