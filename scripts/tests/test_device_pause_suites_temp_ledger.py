#!/usr/bin/env python3
"""GATE: the device-pause suites write to a temp ledger, never the live one (t-3b, 2026-10-23).

Handover 2026-10-23 item 5: "three test suites write fake rows into the live device-pause
ledger (they must use a temp path)". Measured in the live ledger at session start
(workspace/run-logs/device-pause.jsonl, counted from each row's source_argv0):

    648 rows  scripts/tests/test_device_pause.py
    192 rows  scripts/tests/test_scheduler_breaker.py
     76 rows  scripts/tests/test_scheduler_commands.py

The mechanism: scripts/scheduler_commands.py records every device-pause action at the
primitive (_record_device_pause, L152) by appending to the module constant
DEVICE_PAUSE_LEDGER, and the row stamps argv[0] -- so those 916 rows are self-attested test
traffic. test_device_pause.py and test_scheduler_breaker.py redirect sc.PAUSE_FLAG into a
temp dir but never touch sc.DEVICE_PAUSE_LEDGER (PAUSE_FLAG.parent only chooses where the
FLAGS live; the ledger constant is read at call time, not derived from it), and
test_scheduler_commands.py never redirects it at all.

The ledger is append-only evidence for a real operational fact -- who paused which device
when, and who cleared it (the 2026-10-21 "I paused the ThinkPad, why did it start on its own?"
investigation reads it). Test rows poison it: every row from a suite reads as a real pause.

THE FIX THIS SUITE TESTS: scripts/scheduler_commands.py gains a live-ledger guard
(refuse_live_device_pause_ledger) and the three suites point the constant at a temp path
before anything can record. The guard RAISES on the live path, so a suite that forgets to
redirect fails loudly instead of writing (the test_console_run_command_guard.py pattern).

Hermetic: this suite itself writes to nothing; it reads scheduler_commands and greps the
three test files. No device is paused, nothing is recorded.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import scheduler_commands as sc

P = F = 0


def check(label, got, want):
    global P, F
    ok = got == want
    P += ok
    F += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f": got {got!r}, want {want!r}"))


LIVE = ROOT / "workspace" / "run-logs" / "device-pause.jsonl"

# 1. the guard exists and refuses the live path
g = getattr(sc, "refuse_live_device_pause_ledger", None)
check("scheduler_commands exposes refuse_live_device_pause_ledger", g is not None, True)
if g:
    raised = False
    try:
        g(LIVE)
    except (RuntimeError, ValueError):
        raised = True
    check("it raises when handed the live ledger path", raised, True)
    raised = False
    try:
        g(Path("/tmp/x/device-pause.jsonl"))
    except (RuntimeError, ValueError):
        raised = True
    check("it accepts a temp path", raised, False)
    # the reason says what to do with it
    msg = ""
    try:
        g(LIVE)
    except Exception as e:                    # noqa: BLE001 -- the message is the point
        msg = str(e)
    check("the refusal names the fix (point DEVICE_PAUSE_LEDGER at a temp path)",
          "DEVICE_PAUSE_LEDGER" in msg and "temp" in msg.lower(), True)

# 2. the recording primitive refuses the live path
src = (ROOT / "scripts" / "scheduler_commands.py").read_text(encoding="utf-8")
body = src[src.index("def _record_device_pause"):]
body = body[:body.index("\ndef ")]
has_call = "refuse_live_device_pause_ledger" in body
has_open = "open(DEVICE_PAUSE_LEDGER" in body
check("_record_device_pause calls the guard", has_call, True)
check("the guard runs before the file is opened",
      has_call and has_open and body.index("refuse_live_device_pause_ledger") < body.index("open(DEVICE_PAUSE_LEDGER"),
      True)

# 3. every suite that touches a scheduler command redirects the LEDGER, not only the flag
SUITES = ("test_device_pause.py", "test_scheduler_breaker.py", "test_scheduler_commands.py")
for name in SUITES:
    text = (ROOT / "scripts" / "tests" / name).read_text(encoding="utf-8")
    redirects = re.findall(r"\bsc\w*\.DEVICE_PAUSE_LEDGER\s*=|scheduler_commands\.DEVICE_PAUSE_LEDGER\s*=",
                           text, re.IGNORECASE)
    checks_live = bool(re.search(r"refuse_live_device_pause_ledger\(", text))
    check(f"{name} redirects DEVICE_PAUSE_LEDGER to a temp path",
          len(redirects) >= 1, True)
    check(f"{name} asserts the guard (refuse_live_device_pause_ledger) on its target",
          checks_live, True)

# 4. the live ledger is not the target of a module-level assignment in a suite
#    (the redirect must be to a temp dir, checked by the suite files' own text)
for name in SUITES:
    text = (ROOT / "scripts" / "tests" / name).read_text(encoding="utf-8")
    m = re.search(r"DEVICE_PAUSE_LEDGER\s*=\s*([^\n]+)", text)
    if m:
        target = m.group(1)
        bad = "run-logs" in target and "tmp" not in target.lower()
        check(f"{name} does not point the ledger at run-logs", bad, False)

print(f"\n{P} passed, {F} failed")
sys.exit(1 if F else 0)
