#!/usr/bin/env python3
"""A Bash write to a task file is refused; sanctioned Bash invocations still run.

WHY. The task-write guard refuses a hand-written task file on the Write/Edit TOOL only
(.claude/settings.json:24, matcher "Write|Edit"). Its docstring says that exclusion is safe
because taskstore.py "arrives as a Bash call, not as a Write"
(scripts/hooks/guard_task_writes.py:20-23). That is true of the SANCTIONED Bash call --
`python3 .../taskstore.py add ...`, a command with no write operator -- and false of the
channel in general: `cat > workspace/tasks/t999.md <<EOF` is a Bash call that writes a task
file and is indistinguishable from the sanctioned one at the path. RCA d89825d2d2a7 recorded
exactly that (t208, written by heredoc, landed on disk, absent from QUEUE.md, caught only at
push by check_workspace.py).

THE ALLOWED HALF IS THE IMPORTANT HALF. Once settings.json routes Bash to this hook, every
shell command in the workspace passes through it. A case that must NOT be blocked is worth more
than a case that must: a guard that blocks legitimate work gets disabled, and a disabled guard
is worse than none.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "hooks" / "guard_task_writes.py"


def call(tool, key, value):
    payload = json.dumps({"tool_name": tool, "tool_input": {key: value}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload, capture_output=True,
                          text=True, timeout=30, cwd=str(REPO))


def main():
    fails = []

    def check(label, cond, detail=""):
        print(f"  [{'ok' if cond else 'BAD'}]  {label}{'  ' + detail if detail else ''}")
        if not cond:
            fails.append(label)

    rel = "workspace/tasks/t999--written-by-bash.md"
    abso = str(REPO / "workspace/tasks/t999--written-by-bash.md")

    # 1. THE HOLE. Each is a Bash command whose write target is a task file.
    holes = [
        ("heredoc into a task file (the t208 shape)",
         "cat > " + rel + " <<'EOF'\n# t999\nbody\nEOF"),
        ("the same, absolute path",
         "cat > " + abso + " <<'EOF'\nbody\nEOF"),
        ("append redirect, no heredoc", "echo body >> " + rel),
        ("redirect with no space after >", "cat >" + rel + " <<'EOF'\nbody\nEOF"),
        ("quoted target", "cat > '" + rel + "' <<'EOF'\nbody\nEOF"),
        ("tee into a task file", "echo body | tee " + rel),
        ("tee -a append to a task file", "echo body | tee -a " + rel),
    ]
    for label, cmd in holes:
        r = call("Bash", "command", cmd)
        check("BLOCKED: " + label, r.returncode == 2, "rc=%s" % r.returncode)
        check("and it names the sanctioned writer: " + label,
              "taskstore.py add" in r.stderr, r.stderr[:70].replace("\n", " "))

    # 2. THE SANCTIONED AND THE ORDINARY CHANNELS. These must keep working.
    must_work = [
        ("taskstore.py add -- the sanctioned writer, arrives as Bash",
         'python3 projects/app-repo/taskstore.py add "GUARD SELF TEST -- delete me" '
         'local-inference test'),
        ("QUEUE.md is hand-editable, by Bash too",
         "cat > workspace/tasks/QUEUE.md <<'EOF'\n# QUEUE\nEOF"),
        ("a finished task under done/",
         "cat >> workspace/tasks/done/t001--anything.md <<'EOF'\nbody\nEOF"),
        ("READING a task file writes nothing", "cat " + rel),
        ("grep over the tasks directory", "grep -rn t999 workspace/tasks/"),
        ("a redirect to a non-task path", "echo hi > notes.md"),
        ("a task id in a commit message, no write operator",
         'git commit -m "add t999 task file"'),
    ]
    for label, cmd in must_work:
        r = call("Bash", "command", cmd)
        check("NOT blocked: " + label, r.returncode == 0,
              "rc=%s %s" % (r.returncode, r.stderr[:70].replace("\n", " ")))

    # 3. THE OLD CHANNEL SURVIVES: main() must not become Bash-only.
    r = call("Write", "file_path", abso)
    check("a Write to a task file is STILL blocked", r.returncode == 2,
          "rc=%s" % r.returncode)
    r = call("Edit", "file_path", abso)
    check("an Edit to a task file is STILL blocked", r.returncode == 2,
          "rc=%s" % r.returncode)

    # 4. A MALFORMED PAYLOAD MUST NEVER BLOCK THE SESSION.
    r = subprocess.run([sys.executable, str(HOOK)], input="not json", capture_output=True,
                       text=True, timeout=30, cwd=str(REPO))
    check("garbage on stdin does not block anything", r.returncode == 0,
          "rc=%s" % r.returncode)

    for x in fails:
        print("FAIL:", x)
    print("test_task_write_guard_bash:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
