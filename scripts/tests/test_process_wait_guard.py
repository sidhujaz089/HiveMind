#!/usr/bin/env python3
"""The Bash guard must refuse process-liveness waits and leave ordinary work alone.

Each BLOCK case below is a command that was actually typed on 2026-09-07 and cost real time:
a `pgrep -f` that reported a dead process as running, a `ps -p` loop that waited ninety
minutes on a zombie, and a `pkill -f` that killed its own shell and silently dropped the
`git merge` chained after it.

The ALLOW cases matter as much. A guard that also blocks `kill -9 <pid>` or `grep -rn pkill`
is one that gets switched off, which is AGENTS.md rule 9 and the reason this file asserts
both directions.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "hooks" / "guard_process_waits.py"
PASSED = FAILED = 0


def run(cmd, tool="Bash"):
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": cmd}})
    p = subprocess.run([sys.executable, str(HOOK)], input=payload,
                       capture_output=True, text=True, timeout=30)
    return p.returncode


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: exit {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected exit {want}, got {got}")


BLOCK = [
    ("pgrep -f matches the shell running it", "pgrep -f surface_probe.py"),
    ("pkill -f kills the caller",             "pkill -f 'ps -p 2239619'; git merge --no-ff x"),
    ("ps -p loop is blind to zombies",        "while ps -p 2239619 >/dev/null; do sleep 20; done"),
    ("kill -0 loop is blind to zombies",      "until ! kill -0 $P 2>/dev/null; do sleep 5; done"),
]
ALLOW = [
    ("killing an explicit pid",      "kill -9 2239619"),
    ("one-shot inspection by path",  "ps -eo pid,stat,cmd | grep '[p]ython3 /tmp/j.py'"),
    ("waiting on an artifact",       "until [ -s done.marker ]; do sleep 20; done"),
    ("an ordinary commit",           "git commit -q -F msg.txt"),
    ("grepping for the word pkill",  "grep -rn 'pkill' docs/"),
    # Caught within a minute of the hook going live: the commit message INTRODUCING this
    # guard quotes the patterns it blocks, and the guard refused its own commit. A heredoc
    # body is data, not a command.
    ("a commit message that quotes the patterns",
     "git commit -F - <<'EOF'\nfix: pgrep -f matched the shell\nand pkill -f killed it\nEOF"),
    ("writing a doc that mentions ps -p in a loop",
     "cat > notes.md <<'MD'\nwhile ps -p $PID; do sleep 1; done is wrong\nMD"),
]

print("It refuses what lies about liveness:")
for label, cmd in BLOCK:
    check(label, run(cmd), 2)

print("\nAnd leaves everything else alone:")
for label, cmd in ALLOW:
    check(label, run(cmd), 0)

print("\nAnd it only judges Bash:")
check("a Write call passes straight through",
      run("pgrep -f anything", tool="Write"), 0)
check("a malformed payload never blocks",
      subprocess.run([sys.executable, str(HOOK)], input="not json",
                     capture_output=True, text=True, timeout=30).returncode, 0)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
