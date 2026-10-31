#!/usr/bin/env python3
"""Refuse a repo copy made in /tmp -- /tmp on the GX10 is RAM (Jas, 2026-10-17).

On 2026-10-16 /tmp held 17 GB of worktrees and repo copies with ad-hoc names (t1334wt, wt1054,
t1087full) made by Claude sessions and by model run_command calls, and Swift was OOM-killed beside
them. The rule was already a preference ("heavy temp work on disk"); a preference does not stop a
command being typed, so this refuses the command and names the disk path to use instead.

Used two ways: as a Claude Code PreToolUse hook (stdin JSON, exit 2 blocks), and imported by
console.py's run_command so the local models get the same refusal.
"""
import json
import re
import sys

DISK_TMP = "~/.cache/hivemind-tmp"
# A copy of a repository INTO /tmp: worktree add, clone, cp -r/-a, rsync -a. Small files in /tmp are fine.
_START = r"(?:^|[;&|(]\s*|\n\s*)"          # the command itself, not a word inside a string or message
_ARGS = r"[^;&|\n'\"]*"                    # plain arguments within the SAME command segment
_COPY = re.compile(
    _START + r"(?:git\s+(?:-C\s+\S+\s+)?worktree\s+add|git\s+clone|cp\s+-[a-zA-Z]*[raR][a-zA-Z]*|rsync)"
    + _ARGS + r"\s/tmp/(?!claude-)")


# NO WRITING TO /tmp AT ALL -- a STANDING DECISION (Jas, 2026-10-27: "No writing to the TMP folder.
# Good idea."). The guard used to refuse only a repository COPY, on the reasoning that "small files in
# /tmp are fine"; this session then fetched one page to /tmp/hub.html, and small is a property of the
# first file, not of the habit -- the file is RAM, nobody cleans it up, and the thing that follows it
# is 17 GB. Reading /tmp is still fine: `ls /tmp` is not a write.
_WRITE = re.compile(
    # _ARGS BEFORE THE KEYWORD, not [^;|\n]*?: a command that MENTIONS "rsync into /tmp" in a quoted
    # string must not be refused, and a gap class that crosses quotes lets the match start inside one.
    _START + _ARGS + r"(?:" + "|".join([
        r">>?\s*/tmp/(?!claude-)",
        r"\btee\b[^;|\n]*\s/tmp/(?!claude-)",
        r"\b(?:touch|mkdir|install|ln)\b[^;|\n]*\s/tmp/(?!claude-)",
        r"\b(?:curl|wget)\b[^;|\n]*\s(?:-o|--output|-O)\s*/tmp/(?!claude-)",
        # A PLAIN cp/mv/ln INTO /tmp (L1's finding, 2026-10-28: "'cp file /tmp/' still passes the
        # guard"). The copy rule above only matches cp -r/-a, so a single file walked past both.
        # THE DESTINATION, NOT ANY ARGUMENT (L1, 2026-10-28: "'cp /tmp/a ./b' -- copy OUT of /tmp -- is
        # now a false positive"). /tmp must be the LAST thing in the segment, so a source under /tmp is
        # left alone while a destination there is refused. _ARGS (not [^;|\n]*) keeps the match out of
        # quoted strings, which the suite's printf case pins.
        # (?:^|\s) BEFORE /tmp/: Claude's second review (2026-10-28) found 'cp a ./build/tmp/x' refused,
        # because without an anchor /tmp/ matched in the MIDDLE of a path. A destination is an argument,
        # so it starts after whitespace or at the start of the segment.
        r"\b(?:cp|mv|rsync)\b" + _ARGS + r"(?:^|\s)/tmp/(?!claude-)\S*(?=\s*(?:[;&|]|$))",
    ]) + ")")
ALLOW = "HIVEMIND_ALLOW_TMP=1"


def reason(command):
    """The refusal text for a command that writes into /tmp, else None."""
    command = command or ""
    if ALLOW in command:
        return None
    if _COPY.search(command):
        return (f"REFUSED: this copies a repository into /tmp, and /tmp on this machine is RAM -- 17 GB "
                f"of such copies starved Swift on 2026-10-16. Put it on disk instead: {DISK_TMP}/<name> "
                f"(mkdir -p {DISK_TMP} first). Same command, that path in place of /tmp.")
    if _WRITE.search(command):
        return (f"REFUSED: nothing is written to /tmp on this machine -- it is RAM, and this is a "
                f"standing decision (Jas, 2026-10-27). Write it to {DISK_TMP}/ instead: same command, "
                f"that path in place of /tmp. Reading /tmp is fine. A one-off that genuinely must be "
                f"/tmp says so in the command with {ALLOW}, so it is visible rather than silent.")
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    why = reason((data.get("tool_input") or {}).get("command") or "")
    if why:
        print(why, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
