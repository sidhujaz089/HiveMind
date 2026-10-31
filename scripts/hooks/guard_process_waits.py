#!/usr/bin/env python3
"""Refuse a process-liveness wait at the moment it is typed, not at session end.

    PreToolUse hook on Bash. Reads the tool call on stdin; exit 2 blocks it.

WHY A HOOK AND NOT ANOTHER CHECK
--------------------------------
`L-105` recorded this on 2026-08-25 and `scripts/tests/test_self_waiting.py` DETECTS it by
reading /proc. Both existed on 2026-09-07, and the same failure happened three times in one
session anyway:

  * `pgrep -f surface_probe.py` reported RUNNING for a process that had already exited -- it
    was matching the shell that was running the pgrep.
  * `while ps -p 2239619; do sleep 20; done` waited **ninety minutes** on a coder run that had
    finished. The process was a ZOMBIE, and `ps -p` reports a zombie as present.
  * `pkill -f 'ps -p 2239619'` killed the shell issuing it, so the `git merge` chained after it
    never ran and reported nothing.

A test that runs at session end is a correction. This is a refusal, which is the difference
Jas asked for when the same question came up about task files: *"Is there a mechanical fixture
that you can make to prevent hand writing tasks?"*

THE TWO FAILURES ARE DIFFERENT AND BOTH ARE BLOCKED
---------------------------------------------------
1. **Self-match.** `pgrep -f X` / `pkill -f X` search full command lines, and the searching
   shell's own command line contains X. The matcher matches itself. `L-105`.
2. **Zombie-blind liveness.** `ps -p PID` and `kill -0 PID` both succeed for a process that
   has exited and not been reaped. Polling either says "still running" forever. `L-105` does
   not cover this one; it was found on 2026-09-07.

THE FIX GENERALISES, WHICH IS WHY THE MESSAGE SAYS IT
------------------------------------------------------
`L-105`: *wait on the artifact, not on the process.* An artifact cannot be matched by the
thing looking for it and cannot be a zombie.
"""
import json
import re
import sys

# `-f` is the whole problem: it matches against the full command line, which includes ours.
SELF_MATCH = re.compile(r"\b(pgrep|pkill)\b[^|;&]*\s-\w*f", re.I)
# The shell tools this guard judges. The workspace's own shell tool is a shell: a loop typed on
# `mcp__local__bash` is the same loop as one typed on Bash, so the gate reads both.
SHELL_TOOLS = ("Bash", "mcp__local__bash")
# A loop whose exit condition is "does this pid still exist".
LOOP = re.compile(r"\b(while|until)\b", re.I)
LIVENESS = re.compile(r"\bps\s+-p\s+|\bkill\s+-0\s+", re.I)
# The POPULATION half of the property: a process listing in COMMAND position. `ps` counts only
# where a command can start -- the head of the line, after a pipe, semicolon, ampersand, or a
# `$(...)` substitution -- because the letters `ps` sitting inside a grep pattern operand do not
# make that command a listing. Searching the whole text for the string `ps` is exactly what
# would block `grep -rn 'ps' docs/`, which the suite pins as ALLOWED.
POPULATION = re.compile(
    r"(?:^|[|;&(]|\$\()\s*ps\b"                        # a stage that starts with ps
    r"|\bpidof\b"                                      # pidof is a listing by name
    r"|\b(?:awk|egrep|grep)\b[^\n]*?/proc/\S*cmdline"  # a /proc scan: the glob IS the listing
    r"|" + SELF_MATCH.pattern,                         # pgrep/pkill carrying an -f operand
    re.I,
)
# The filter the population is run through, and the tokens that filter is written with.
FILTER = re.compile(r"\b(?:awk|egrep|grep|pgrep|pkill)\b", re.I)
TOKEN = re.compile(r"\"[^\"\\]*\"|'[^'\\]*'|[^\s]+")


# A SELECTOR, seen as the property needs it: the operand that filters a process POPULATION.
# `pgrep -f X` and a `grep -c X` on a line that names a listing are the same act -- the verb is
# not the fact, the pattern is.
SELECTOR = re.compile(
    r"\b(?:pgrep|pkill)\b[^\n|;&]*?\s-\w*f\s+(\"[^\"\n]*\"|'[^'\n]*'|[^\s;&|<>()]+)", re.I)
COUNTING_GREP = re.compile(
    r"\bgrep\b[^\n|;&]*?\s-[\w-]*[cq][\w-]*\s+(\"[^\"\n]*\"|'[^'\n]*')", re.I)
# The text a selector runs in. A heredoc body is data -- until the command that wrote it also
# runs the file the body went into. HEREDOC is strip_heredocs' delimiter test, kept in one place
# so the two never disagree about where a body ends.
HEREDOC = re.compile(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?")
WRITE_TARGET = re.compile(r">\s*([^\s;&|<]+)")
RUNNER = re.compile(r"(?:^|[|;&(]|\$\()\s*(?:(?:nohup|setsid)\s+)?(?:bash|sh|source|\.)"
                    r"\s+([^\s;&|<>()]+)", re.I | re.M)
# What makes a loop a LAUNCHED loop: backgrounding, and `bash -c`/`sh -c`, which run what they
# carry. A loop written into a file and never started is not a watcher yet.
LAUNCH = re.compile(r"&&?|\bnohup\b|\bsetsid\b|\bdisown\b|\b(?:bash|sh)\s+-\w*c\b", re.I)


def strip_heredocs(cmd):
    """Remove heredoc BODIES before matching. They are data, not commands.

    Found within a minute of this hook going live: the commit message introducing it quotes
    the very patterns it blocks, so the guard refused its own commit. A guard that blocks
    writing about the problem is one that gets switched off, which is AGENTS.md rule 9 --
    the same reason the test asserts what must be ALLOWED as carefully as what must not.
    """
    out, lines, i = [], cmd.split("\n"), 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = re.search(r"<<-?\s*'?\"?([A-Za-z_][A-Za-z0-9_]*)'?\"?", line)
        if m:
            end = m.group(1)
            i += 1
            while i < len(lines) and lines[i].strip() != end:
                i += 1                          # drop the body
        i += 1
    return "\n".join(out)


def _bodies(cmd):
    """[(start, end, target)] -- the half-open line range of every heredoc body in the command,
    and the file that line writes. The write-target is what separates a watcher from a document:
    a body is data until the command that wrote it also RUNS the file the body went into.
    """
    lines = cmd.split("\n")
    found, i = [], 0
    while i < len(lines):
        m = HEREDOC.search(lines[i])
        start = i + 1
        if m:
            target = WRITE_TARGET.search(lines[i])
            i += 1
            while i < len(lines) and lines[i].strip() != m.group(1):
                i += 1
            found.append((start, i, target.group(1) if target else ""))
        i += 1
    return found


def self_matching(cmd):
    """Why this command LAUNCHES a loop whose exit test can see the loop itself, else None.

    The property, not the verb: a selector -- `pgrep -f X`, or a `grep -c X` on a line that
    names a process listing -- is refused when its pattern, COMPILED AS A REGULAR EXPRESSION,
    matches the text of the command that launches it. Compiled on the pattern and matched
    against the text: that is the only reason the bracketed form the refusal below recommends is
    legal, because a command carrying `[o]llama pull` contains no literal `ollama pull` to
    match. A substring test would refuse the guard's own remedy.

    The POPULATION is the text a selector runs in: every line outside a heredoc body, plus the
    body of a heredoc whose write-target file a runner names outside that body. A pattern the
    regex engine rejects is compiled as its own literal text and the failure is named in the
    refusal, so a pattern that will not compile is never a way through the gate.
    """
    lines = cmd.split("\n")
    bodies = _bodies(cmd)
    inside = {n for start, end, _ in bodies for n in range(start, end)}
    outside = "\n".join(l for n, l in enumerate(lines) if n not in inside)
    population = outside
    for start, end, target in bodies:
        if target and target in RUNNER.findall(outside):
            population += "\n" + "\n".join(lines[start:end])
    # Nothing written here is a watcher until something launches the loop: `&`, nohup,
    # `bash -c`, or a `while`/`until` typed outside a heredoc body.
    if not (LAUNCH.search(population) or LOOP.search(population)):
        return None
    selectors = [m.group(1) for m in SELECTOR.finditer(population)]
    for line in population.split("\n"):
        # A count over TEXT is not a count over a PROCESS POPULATION: `grep -rn 'ps' docs/`
        # searches words, and the suite pins that as allowed.
        if POPULATION.search(line):
            selectors += [m.group(1) for m in COUNTING_GREP.finditer(line)]
    for quoted in selectors:
        pattern = quoted
        if len(pattern) > 1 and pattern[0] == pattern[-1] and pattern[0] in "\"'":
            pattern = pattern[1:-1]
        note = ""
        try:
            probe = re.compile(pattern)
        except re.error as exc:
            probe = re.compile(re.escape(pattern))
            note = (" -- `" + pattern + "` is not a valid regular expression (" + str(exc)
                    + "), so it is matched as literal text")
        if pattern and probe.search(cmd):
            return ("This launches a loop whose exit test filters a process POPULATION by the "
                    "pattern `" + pattern + "`" + note + ", and that pattern matches text of its "
                    "own command line: the loop counts the shell doing the waiting, so the exit "
                    "condition can never become false.")
    return None


def selector_pattern(cmd):
    """Return (pattern, listing) for a count over a process listing, else None.

    The POPULATION must be present -- `grep -rn 'ps' docs/` searches text, it does not list
    processes, and the suite pins that case as ALLOWED. The PATTERN is the operand the filter is
    run with: the first non-option operand of a grep/egrep/awk, because the flags come first
    (`grep -c \"x\"` filters on x, not on `-c`), or the operand of a `-f` on pgrep/pkill. The
    pair is returned so a refusal can name the pattern it extracted instead of guessing.

    Called on the text strip_heredocs already reduced: a count written into a file by a heredoc
    and never run is data, not a command, and stays allowed.
    """
    found = POPULATION.search(cmd)
    if not found:
        return None
    m = FILTER.search(cmd)
    if not m:
        return None
    for tok in TOKEN.findall(cmd[m.end():]):
        if tok.startswith("-"):
            continue          # -c, -w, -q, -n, -rn, -f and -- are flags, not the pattern
        if len(tok) > 1 and tok[0] == tok[-1] and tok[0] in "\"'":
            tok = tok[1:-1]
        if tok:
            return (tok, found.group(0))
    return None


def bracket_remedy(pattern):
    """The bracketed rendering of a refused pattern, for the refusal text (t1507).

    Idempotent: a pattern that already opens with a bracket class is returned unchanged.
    Re-wrapping its first letter would print "[[s]]" -- which is not the remedy this file
    recommends, and a guard that refuses its own remedy is a guard that gets switched off.
    """
    if pattern.startswith("["):
        return pattern
    at = next((i for i, ch in enumerate(pattern) if ch.isalnum()), len(pattern))
    return pattern[:at] + "[" + pattern[at:at + 1] + "]" + pattern[at + 1:]


# A FOREGROUND setsid NEVER WAITS HERE (RCA rca-setsid-exit-code-2026-10-29). When the caller leads its
# process group -- this harness's shell does: PID == PGID -- setsid FORKS, runs the program in the child's
# new session and exits 0 at once. So `setsid CMD; echo $?` reports 0 whatever CMD did, and a log read
# right after it may not be finished. A suite that crashed read as green on 2026-10-29. `-w`/`--wait`
# makes setsid wait and pass the program's status through; a backgrounded launch (`&`) never claimed one.
SETSID = re.compile(r"(?:^|[;&|(\n]|\$\()\s*(?:(?:do|then|else|time)\s+)?(?:nohup\s+)?setsid\b([^;&|\n]*)")


def setsid_without_wait(cmd):
    """Why this command runs setsid in the FOREGROUND without -w, else None."""
    for m in SETSID.finditer(cmd):
        args = m.group(1)
        if re.search(r"(?:^|\s)(?:-\w*w\w*|--wait)\b", args):
            continue
        rest = cmd[m.end():m.end() + 2]
        if rest.startswith("&") and not rest.startswith("&&"):
            continue                                   # backgrounded: no status was ever claimed
        return ("`setsid` without `-w` returns 0 at once when the shell leads its process group\n"
                "(this one does), so the program's exit status is LOST and its output may be\n"
                "unfinished when the next command reads it. Use `setsid -w CMD` to wait and get the\n"
                "real status, or write the status to a .done file inside the job.")
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:                                        # noqa: BLE001
        return 0                    # never block on a malformed payload
    if data.get("tool_name") not in SHELL_TOOLS:
        return 0
    raw = (data.get("tool_input") or {}).get("command") or ""
    cmd = strip_heredocs(raw)

    why = None
    # The property belongs to the SELECTOR, not to the verb: a count over a process listing is
    # refused when its pattern can match the command running the count. The pattern is read as a
    # REGULAR EXPRESSION -- a substring test would block the bracket form this file recommends --
    # and a pattern the engine rejects is judged as literal text, so a pattern that will not
    # compile is never a way through the gate.
    pair = selector_pattern(cmd)
    pattern, listing = pair if pair else (None, None)
    self_reference = False
    if pattern is not None:
        try:
            self_reference = re.search(pattern, cmd) is not None
        except re.error:
            self_reference = re.search(re.escape(pattern), cmd) is not None
    # RELAXED: a `pgrep -f` / `pkill -f` whose pattern cannot match this command's own text is
    # not self-referential. `pgrep -f '[p]robe.py'` is the remedy the refusal printed below
    # recommends, and a guard that refuses its own remedy is a guard that gets switched off.
    if SELF_MATCH.search(cmd) and (pattern is None or self_reference):
        why = ("`pgrep -f` / `pkill -f` match the shell that is running them, because that\n"
               "shell's own command line contains the pattern (L-105). This reports a dead\n"
               "process as alive, and `pkill -f` kills the caller -- which on 2026-09-07\n"
               "silently dropped a `git merge` chained after it.")
    elif LOOP.search(cmd) and LIVENESS.search(cmd):
        why = ("`ps -p PID` and `kill -0 PID` both SUCCEED for a zombie -- a process that has\n"
               "exited and not been reaped. Polling either never exits. On 2026-09-07 this\n"
               "waited ninety minutes on a coder run that had finished in twenty.")
    if self_reference:
        # The refusal names the pattern it extracted and the bracketed form that fixes it: a
        # refusal that says only "not that" leaves the reader to invent the remedy.
        bracketed = bracket_remedy(pattern)
        why = (why + "\n\n" if why else "") + (
            "This command counts a process POPULATION -- " + listing + " -- and filters it by\n"
            "the pattern \"" + pattern + "\", which can match the text of its own command line\n"
            "(L-105). Bracket the first character -- \"" + bracketed + "\" -- so the count\n"
            "stops seeing the shell that runs it.")
    # THE PROPERTY, NOT THE VERB: a watcher written into a file by this very command and started
    # by it, a loop whose exit test scans /proc instead of naming pgrep, and a loop typed on the
    # workspace's own shell tool all reach the same endpoint. The shape rules above see none of
    # them -- strip_heredocs deletes the first, the second never says pgrep, and the third used
    # to leave through the tool gate.
    watcher = self_matching(raw)
    if watcher:
        why = (why + "\n\n" if why else "") + watcher
    lost = setsid_without_wait(cmd)
    if lost and not why:
        print(f"BLOCKED: {lost}", file=sys.stderr)
        return 2
    if lost:
        why = why + "\n\n" + lost
    if not why:
        return 0

    print(
        f"BLOCKED: this waits on a PROCESS, and process liveness lies here.\n\n{why}\n\n"
        "Wait on the ARTIFACT instead (L-105). An artifact cannot match the thing looking\n"
        "for it and cannot be a zombie:\n\n"
        "A process selector is refused for the same reason: the text of its own command line\n"
        "is in the population it counts.\n\n"
        "  # the job's own output, which stops changing when the job stops\n"
        "  until [ -s done.marker ]; do sleep 20; done\n\n"
        "  # or: has the log gone quiet?\n"
        "  stat -c %y run.log        # compare against `date`\n\n"
        "  # or: let the harness do it -- run_in_background notifies on exit\n\n"
        "To INSPECT rather than wait, match on a path, not a bare name, and read the state:\n"
        "  ps -eo pid,stat,etime,cmd | grep '[p]ython3 /full/path/to/job.py'\n"
        "  (a STAT of Z means finished-but-unreaped, which is NOT running)\n\n"
        "To KILL, use the explicit pid: `kill -9 <pid>` -- never `pkill -f <name>`.",
        file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
