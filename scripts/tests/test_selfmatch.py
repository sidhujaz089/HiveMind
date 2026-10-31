#!/usr/bin/env python3
"""selfmatch.py must catch the four real 2026-08-26 self-matching watchers, and refuse.

Asserted in the failing direction throughout: the whole value of this checker is the
SELF-MATCH verdict, so every case that matters here is a refusal.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import selfmatch  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def verdicts(cmd):
    return [v for v, *_ in selfmatch.findings(cmd)]


def main():
    print("The checker's own self-test:")
    report("every declared case behaves as declared", selfmatch.self_test(), 0)

    print("\nThe four real failures are all refused:")
    real = [
        'until ! pgrep -f "ollama pull qwen3.6" >/dev/null; do sleep 15; done',
        'until ! pgrep -f "claude -p Please implement" >/dev/null 2>&1; do sleep 10; done',
        'while pgrep -f "ollama pull qwen3.6" >/dev/null 2>&1; do sleep 15; done',
        'while pgrep -f "ollama pull" >/dev/null || pgrep -f "ollama.*install.sh"; do sleep 60; done',
    ]
    for i, cmd in enumerate(real, 1):
        report(f"failure {i} is SELF-MATCH", selfmatch.SELF in verdicts(cmd), True)

    print("\nIt does not cry wolf:")
    report("a marker-file wait is clean",
           selfmatch.SELF in verdicts('until [ -f /tmp/done ]; do sleep 5; done'), False)
    report("a pid wait is clean",
           selfmatch.SELF in verdicts('while kill -0 "$PID" 2>/dev/null; do sleep 5; done'), False)
    report("excluding self with $$ is still reported (it is a hint, not a proof)",
           selfmatch.SELF in verdicts('pgrep -f worker | grep -v $$'), True)

    print("\nA runtime-built pattern is UNKNOWN, never a pass:")
    report("a $VAR pattern is not silently approved",
           selfmatch.UNKNOWN in verdicts('pgrep -f "$NAME"'), True)
    report("  and is NOT reported safe", selfmatch.SAFE in verdicts('pgrep -f "$NAME"'), False)

    print("\npkill is called out as worse than pgrep:")
    _, _, _, why = selfmatch.findings('pkill -f "my-daemon" && echo my-daemon')[0]
    report("the pkill message says it signals itself", "signals ITSELF" in why, True)

    print("\nFile scanning:")
    tmp = Path(tempfile.mkdtemp())
    try:
        bad = tmp / "bad.sh"
        bad.write_text('#!/bin/bash\nuntil ! pgrep -f "my-job" >/dev/null; do sleep 5; done\n')
        report("a self-matching loop in a file is found", selfmatch.scan_file(bad), 1)

        commented = tmp / "ok.sh"
        commented.write_text('#!/bin/bash\n# do not use: pgrep -f "my-job" in a loop\nsleep 1\n')
        report("a comment describing the trap is NOT flagged",
               selfmatch.scan_file(commented), 0)

        # The exclusion must be narrow: only the checker's own fixtures, by name.
        fixture = tmp / "selfmatch.py"
        fixture.write_text('until ! pgrep -f "x" ; do sleep 1; done\n')
        report("the checker's own fixture file is skipped", selfmatch.scan_file(fixture), 0)
        other = tmp / "selfmatch_helper.py"
        other.write_text('until ! pgrep -f "x" ; do sleep 1; done\n')
        report("  but a similarly-named file is NOT skipped", selfmatch.scan_file(other), 1)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nThe CLI exits non-zero on a self-match:")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "selfmatch.py"), "--cmd",
                        'until ! pgrep -f "zz" ; do sleep 1; done'], capture_output=True, text=True)
    report("exit code is 1", r.returncode, 1)
    report("and it says it is refusing", "Refusing" in r.stdout, True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
