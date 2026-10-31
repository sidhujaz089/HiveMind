#!/usr/bin/env python3
"""Refuse a hand-written task file at the moment it is attempted, not at push time.

    PreToolUse hook on Write/Edit. Reads the tool call on stdin; exit 2 blocks it.

WHY A HOOK AND NOT ANOTHER CHECK
--------------------------------
`check_workspace.py` already catches this: a task file that is not in QUEUE.md fails "QUEUE.md
and the task files name the same set". But it catches it at PUSH, after the file is written,
the body composed and the work done. Jas, 2026-09-04: *"That has happened before. Is there a
mechanical fixture that you can make to prevent hand writing tasks?"* -- and it had, twice in
one session by me.

Detection after the fact is a correction. This is a refusal: the write does not land, and the
message says which command to use instead. That is the difference between a rule you learn by
being told off and one you cannot break.

WHY IT BLOCKS THE **TOOL** AND NOT THE PATH
-------------------------------------------
`taskstore.py` writes these same files, and must keep working. It does so from a subprocess,
so it arrives as a Bash call, not as a Write. Blocking the Write/Edit TOOL on this path
therefore separates the two exactly: the sanctioned writer is untouched, and the hand edit --
which is the one that skips the lock and leaves QUEUE.md stale -- is refused.

The lock is the real reason. `taskstore.py`'s own help says it: *"Use this rather than editing
the files by hand: it takes the lock, and a hand edit does not."* Two writers and no
coordination is `L-046`, and this repository has lost rows to it before.

A CONTRADICTION IN THE FIRST VERSION, WORTH RECORDING
----------------------------------------------------
Its refusal message ended "create it with `add`, then edit the file the tool made" -- advice
this hook BLOCKS, since it refuses Edit on a task file too. Caught within an hour of writing
it, by trying to follow it. A guard whose instructions cannot be followed is one people route
around, and routing around is the behaviour it exists to stop.

WHAT IT DELIBERATELY ALLOWS
---------------------------
`workspace/tasks/QUEUE.md` itself, and anything under `done/`. Those are moved and rewritten by
the tool, and a session that needs to repair one by hand should be able to. The narrow rule is
what keeps this from becoming a hook people disable.
"""
import json
import re
import sys

ALLOW = (re.compile(r"workspace/tasks/QUEUE\.md$"),
         re.compile(r"workspace/tasks/done/"))
TASKFILE = re.compile(r"workspace/tasks/t\d+[^/]*\.md$")

# The shell tools a command can arrive on. The sibling guard (guard_process_waits.py)
# treats Bash and the workspace shell as one channel; this file keeps its own copy of that
# pair deliberately, because the shape is two strings and importing the sibling would couple
# unrelated guards.
SHELL_TOOLS = ("Bash", "mcp__local__bash")

# A write operator in a shell command and the first path token it writes to. The operators are
# redirection (`>` / `>>`) and `tee` with its flags. An optional quote may hug the target.
# Group 1 is a redirect target; group 2 is a `tee` operand. `bash_write_target` returns whichever
# matched, so a command with no write operator (a bare cat, a grep, `taskstore.py add`) gives "".
TASK_WRITE = re.compile(
    r"""(?:^|[\s;&|])(?:>{1,2}\s*["']?([^\s"'<>&|;]+)|\btee\b(?:\s+-\S+)*\s+["']?([^\s"'<>&|;]+))"""
)


def bash_write_target(command: str) -> str:
    """Return the first file a shell command redirects to or tees into.

    An empty string means the command writes no file at a path -- a bare `cat`, a `grep`,
    and the sanctioned `taskstore.py add` call all return empty.
    """
    match = TASK_WRITE.search(command)
    if not match:
        return ""
    return match.group(1) or match.group(2) or ""



def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:                                        # noqa: BLE001
        return 0            # a hook that cannot parse its input must not block the session
    if payload.get("tool_name") in SHELL_TOOLS:
        path = bash_write_target(str((payload.get("tool_input") or {}).get("command") or ""))
    else:
        path = str((payload.get("tool_input") or {}).get("file_path") or "")

    if not path or not TASKFILE.search(path):
        return 0
    if any(p.search(path) for p in ALLOW):
        return 0

    print(
        f"BLOCKED: {path} is a task file, and task files are not written by hand.\n"
        f"\n"
        f"  python3 projects/app-repo/taskstore.py add \"<title>\" <area> <source>\n"
        f"  python3 projects/app-repo/taskstore.py note <id> \"<text>\"\n"
        f"  python3 projects/app-repo/taskstore.py file <id> <area> <kind> <needs>\n"
        f"  python3 projects/app-repo/taskstore.py status <id> <queued|doing|blocked|done>\n"
        f"\n"
        f"taskstore.py takes the lock and updates QUEUE.md; a hand-written file does neither,\n"
        f"so the task exists on disk and is INVISIBLE IN THE PLAN -- including on the phone at\n"
        f"http://100.64.0.10:8091/plan, which is where Jas actually reads it.\n"
        f"\n"
        f"For a long body: `add` it, then `note` the sections. If a task genuinely needs a\n"
        f"structured body that `note` cannot express, say so and change taskstore.py -- do not\n"
        f"work around this by hand, which is how QUEUE.md goes stale in the first place.",
        file=sys.stderr)
    return 2                # 2 blocks the tool call and shows this to the model


if __name__ == "__main__":
    sys.exit(main())
