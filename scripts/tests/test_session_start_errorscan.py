#!/usr/bin/env python3
"""Wiring oracle for t276: session_start must RUN the error scanner and report it.

Run:  python3 scripts/tests/test_session_start_errorscan.py
Exit 0 if all cases pass, 1 otherwise.

This oracle is about the ROUTINE PATH, not the scanner's detection (that lives in
test_error_scan.py). With fakes only, it pins that:
  1. a repo seeded with a known-bad row shows that FINDING IN session_start's OUTPUT
     (not that the string 'error_scan' is in a file, which passes on a comment);
  2. a scanner that raises is REPORTED (type + message) and does not take start down;
  3. every source absent -> UNEXAMINED, never a clean pass (the 'silent zero' decay);
  4. the section runs in-process, offline: it must not spawn a subprocess.

Fail-first: on the current code _render_error_scan / _error_scan_section do not exist
and main() never calls the scanner, so every case is red for the RIGHT reason (not
wired), not because error_scan detects badly.
"""
import contextlib
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import error_scan            # real module, so we can swap its scan() to make it raise
import session_start

PASSED = FAILED = 0

def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: got {got!r}, want {want!r}")

SEED_EVIDENCE = "SEED-DO-NOT-MISS done_reason length"
SEED = {"kind": "truncated-output", "source": "workspace/agent-runs.jsonl",
        "locator": "seed-1", "evidence": SEED_EVIDENCE, "at": "2026-09-09T00:00:00Z"}

def render(findings, skipped):
    try:
        return "\n".join(session_start._render_error_scan(findings, skipped))
    except Exception as e:
        return f"__RENDER_UNAVAILABLE__ {type(e).__name__}: {e}"

def section_capture():
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            session_start._error_scan_section()
        return None, buf.getvalue()
    except Exception as e:
        return e, buf.getvalue()

print("1. A FINDING must render its evidence verbatim, with a count and the source named:")
t = render([SEED], [])
check("evidence is in the rendered output (not a comment)", SEED_EVIDENCE in t, True)
check("a count is printed", "1" in t, True)
check("the source is named", "workspace/agent-runs.jsonl" in t, True)

print("\n3. THREE OUTCOMES, not two: 'looked and clean' vs 'could not look' must differ:")
t = render([], ["workspace/agent-runs.jsonl"])
low = t.lower()
check("all-sources-absent reads UNEXAMINED / could-not-read",
      ("unexamined" in low) or ("could not read" in low) or ("not read" in low), True)
check("and it does NOT read as clean", "clean" in low, False)
t = render([], [])
check("examined-and-empty reads clean", "clean" in t.lower(), True)

print("\n2. A scanner that raises must be reported (type + message) and not take start down:")
def _boom(*a, **k):
    raise RuntimeError("scanner broke on purpose")
orig_scan = error_scan.scan
error_scan.scan = _boom
try:
    raised, out = section_capture()
finally:
    error_scan.scan = orig_scan
check("the section did not propagate the exception", raised is None, True)
low = out.lower()
check("the report says broken", "broken" in low or "broke" in low, True)
check("the CAUGHT TYPE is recorded (not swallowed)", "RuntimeError" in out, True)
check("the MESSAGE is recorded", "scanner broke on purpose" in out, True)

print("\n4. The section must run in-process; a subprocess call is a contract violation:")
def _deny(*a, **k):
    raise AssertionError("error_scan section spawned a subprocess")
orig_run = session_start.subprocess.run
session_start.subprocess.run = _deny
orig_scan2 = error_scan.scan
error_scan.scan = lambda *a, **k: [SEED]
try:
    raised, out = section_capture()
finally:
    session_start.subprocess.run = orig_run
    error_scan.scan = orig_scan2
check("the section completed without a subprocess", raised is None, True)
check("and still printed the finding", SEED_EVIDENCE in out, True)

print("\n1e. END-TO-END: a repo seeded with a known-bad row shows the finding in")
print("    session_start's OUTPUT -- proving main() calls the scanner, not a comment:")
td = Path(tempfile.mkdtemp())
scripts = td / "scripts"
scripts.mkdir()
shutil.copy(session_start.__file__, scripts / "session_start.py")
(scripts / "error_scan.py").write_text(
    "SEED = {\n"
    "    'kind': 'truncated-output',\n"
    "    'source': 'workspace/agent-runs.jsonl',\n"
    "    'locator': 'seed-1',\n"
    "    'evidence': '" + SEED_EVIDENCE + "',\n"
    "    'at': '2026-09-09T00:00:00Z',\n"
    "}\n"
    "def scan(paths=None, since=None):\n"
    "    return [SEED]\n"
    "def skipped():\n"
    "    return []\n",
    encoding="utf-8")
r = subprocess.run([sys.executable, str(scripts / "session_start.py")],
                   capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
check("the seeded finding appears in session_start's stdout", SEED_EVIDENCE in r.stdout, True)
check("and the run did not crash", r.returncode in (0, 1), True)

print(f"\ntest_session_start_errorscan: {PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
