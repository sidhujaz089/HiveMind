#!/usr/bin/env python3
"""A foreground setsid without -w is refused: its exit status is lost (RCA rca-setsid-exit-code-2026-10-29).

This harness's shell leads its process group, so `setsid CMD` forks and returns 0 at once. On 2026-10-29 a
suite that crashed read as `rc 0` and was reported green. Both directions are pinned: the lying forms are
refused, the honest forms (-w/--wait, a backgrounded launch, the word in prose) pass.
"""
import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "guard_process_waits.py"
FAILED = 0


def verdict(cmd):
    r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": cmd}}), capture_output=True, text=True, timeout=30)
    return r.returncode, r.stderr


def check(name, cmd, refused):
    global FAILED
    rc, err = verdict(cmd)
    ok = (rc == 2) == refused and (not refused or "setsid" in err and "-w" in err)
    FAILED += not ok
    print(f"  {'ok ' if ok else 'BAD'}  {name}: rc={rc}")


check("bare setsid then $? is refused", 'setsid sh -c "exit 3"; echo $?', True)
check("setsid inside a loop body is refused (the 2026-10-29 command)",
      "for t in a b; do setsid timeout 300 python3 $t > l; echo rc $?; done", True)
check("setsid after then is refused", "if true; then setsid y; fi", True)
check("setsid -w passes", 'setsid -w sh -c "exit 3"; echo $?', False)
check("setsid --wait passes", "cd x && setsid --wait python3 y", False)
check("a backgrounded launch passes", "setsid nohup x.sh > /dev/null 2>&1 < /dev/null & disown", False)
check("the word in prose passes", "echo setsid is a word", False)
print("test_process_wait_setsid:", "ok" if not FAILED else "FAILED")
sys.exit(1 if FAILED else 0)
