#!/usr/bin/env python3
"""The task-write guard is registered for the SHELL tools, not only the file tools.

WHY. The guard script can refuse a Bash write, and the harness asks it about a tool only if
.claude/settings.json's PreToolUse matcher names that tool. Today the entry reads
matcher "Write|Edit" (.claude/settings.json:24), so `cat > workspace/tasks/t999.md` reaches no
guard at all -- the script can be perfect and still never be consulted. The sibling entry at
.claude/settings.json:33 names both shell tools for guard_process_waits.py, and that file's
own comment records why both are named: a matcher naming only one of the two leaves the other
unguarded.

The matcher is a REGEX matched against the tool name, so the faithful check is
re.fullmatch(matcher, tool) for each real tool name -- not "does the word Bash appear in the
file", which a comment would satisfy.
"""
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SETTINGS = REPO / ".claude" / "settings.json"
SCRIPT = "guard_task_writes.py"
TARGETS = ("Write", "Edit", "Bash", "mcp__local__bash")


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'BAD'}]  {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(label)

    cfg = json.loads(SETTINGS.read_text())
    hooks = cfg["hooks"]["PreToolUse"]
    guard = [str(e.get("matcher", "")) for e in hooks if SCRIPT in json.dumps(e)]

    check("the task-write guard is registered on PreToolUse", bool(guard),
          "no entry mentions " + SCRIPT)
    for tool in TARGETS:
        check("and it fires for the tool " + tool,
              any(re.fullmatch(p, tool) for p in guard),
              "matchers seen: " + " | ".join(guard))

    # NON-REGRESSION: widening the task guard must not displace the process-wait guard, which
    # already occupies the Bash matcher.
    pw = [str(e.get("matcher", "")) for e in hooks
          if "guard_process_waits.py" in json.dumps(e)]
    check("guard_process_waits.py is still registered for Bash",
          any(re.fullmatch(p, "Bash") for p in pw), "matchers: " + " | ".join(pw))

    for x in fails:
        print("FAIL:", x)
    print("test_task_write_guard_registration:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
