#!/usr/bin/env python3
"""The process-wait guard must be armed on the tool a session here actually types with.

The task's cause: the matcher string is literally "Bash" (settings.json:33) and the tool a
session in this workspace uses is mcp__local__bash. A guard whose population does not contain the
tool is a guard believed to be armed -- AGENTS.md rule 10, and the reason this fixture reads the
REAL settings file rather than a synthetic dict: a build that changes nothing the harness reads
passes a fixture built from what its author imagined the file looks like.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SETTINGS = REPO / ".claude" / "settings.json"
GUARD = "scripts/hooks/guard_process_waits.py"
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print("  [ok]   " + label + ": " + repr(got))
    else:
        FAILED += 1
        print("  [BAD]  " + label + ": expected " + repr(want) + ", got " + repr(got))


def entry_for(settings, needle):
    """The PreToolUse entry whose command runs `needle`, as (matcher, command), or (None, None)."""
    for group in (settings.get("hooks") or {}).get("PreToolUse") or []:
        for hook in group.get("hooks") or []:
            if needle in str(hook.get("command") or ""):
                return group.get("matcher"), str(hook.get("command") or "")
    return None, None


def matcher_set(matcher):
    return {part.strip() for part in str(matcher or "").split("|") if part.strip()}


def main():
    print("The process-wait guard is armed on every tool a session can type a shell command with:")
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    matcher, command = entry_for(data, GUARD)
    check("the settings file still carries the process-wait hook", bool(command), True)
    check("its matcher covers mcp__local__bash, the workspace's own bash tool",
          "mcp__local__bash" in matcher_set(matcher), True)
    check("its matcher still covers Bash, the built-in tool", "Bash" in matcher_set(matcher), True)
    check("the matcher is exactly the two shell tools, as a set", matcher_set(matcher),
          {"Bash", "mcp__local__bash"})
    check("and it runs the script that enforces L-105", GUARD in command, True)

    print("\nAnd the sibling guard on the same hook is untouched:")
    task_matcher, task_command = entry_for(data, "guard_task_writes.py")
    check("the task-write guard is still matched to Write|Edit", matcher_set(task_matcher),
          {"Write", "Edit"})
    check("and still runs its own script", "guard_task_writes.py" in task_command, True)

    print("\n" + str(PASSED) + " passed, " + str(FAILED) + " failed\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
