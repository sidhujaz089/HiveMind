#!/usr/bin/env python3
"""Refuse a count over a process listing whose pattern is in its own command line.

Class (workspace/rca/410fbd3665d0.md, task t788): "Trying to confirm the probe was dead, my
/proc scan kept reporting one left -- it was matching my own shell, whose command line
contained the string probe.py." The count is the same self-match as `pgrep -f probe.py` in a
costume the admission gate cannot see. scripts/hooks/guard_process_waits.py:41 matches the
literal verbs pgrep/pkill carrying a -f operand, and lines 43-44 match a while/until loop paired
with ps -p / kill -0. A one-shot count names no pgrep and no loop, so no rule fires and the very
command that produced the false "one left" verdict reaches main's `if not why: return 0`
(lines 88-89) and is never told the remedy this same file prints at lines 100-102.

THE PROPERTY, NOT THE VERB. A command that selects a process POPULATION (ps in command position,
a /proc/.../cmdline listing, pidof, pgrep -f) and filters that population by a PATTERN is refused
UNLESS the pattern, INTERPRETED AS A REGULAR EXPRESSION, cannot match the text of the command
doing the selecting. As a regular expression, not as a substring: the bracket form the guard's own
message recommends (line 101, grep '[p]ython3 /full/path/to/job.py') is legal precisely because
[p]robe.py is not a substring of the command that contains it. A build using a substring test
fails the ALLOW cases below.

WHY THIS FILE IS NEW. scripts/tests/test_process_wait_guard.py is the frozen 2026-09-07 suite
(13 assertions, green on main) and scripts/bridge.py:129-131 refuses to overwrite an existing
suite with different text, so the property gets its own file. That suite's 13 assertions are
re-pinned here as the contract that must not regress, and scripts/tests/test_process_wait_selfmatch.py
(t806) pins the loop form of the same property against the same file. The two gates must agree:
both require a bracketed pgrep -f pattern to be ALLOWED and a self-matching one to be BLOCKED.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "hooks" / "guard_process_waits.py"
SQ, DQ = chr(39), chr(34)
PAT = "probe.py"
BRACKETED = "[p]robe.py"
# Built by concatenation so no reader mistakes a fixture string for a live listing being parsed.
PS = "ps"
PS_AUX = PS + " aux"
PASSED = FAILED = 0

# The RCA's command, and the verdict line it was printed on.
COUNT = "grep -c " + DQ + PAT + DQ + " /proc/*/cmdline 2>/dev/null"
COUNT_VERDICT = ('printf ' + DQ + PAT + ' left: %s' + DQ + ' "$('
                 'grep -c ' + DQ + PAT + DQ + ' /proc/*/cmdline 2>/dev/null'
                 ' | paste -sd+ - | bc)"')


def run_full(cmd, tool="Bash"):
    """The hook exactly as the harness runs it: the tool call on stdin, the verdict in the code."""
    payload = json.dumps({"tool_name": tool, "tool_input": {"command": cmd}})
    return subprocess.run([sys.executable, str(HOOK)], input=payload,
                          capture_output=True, text=True, timeout=30)


def run(cmd, tool="Bash"):
    return run_full(cmd, tool).returncode


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print("  [ok]   " + label)
    else:
        FAILED += 1
        print("  [BAD]  " + label + ": expected " + repr(want) + ", got " + repr(got))


# A refusal must name the pattern it extracted, render THAT pattern's bracketed form, cite L-105
# and offer the artifact. A generic message passes a suite that only checks the prefix, which is
# the oracle-weak class (t471, t297, t370; RCA t613).
MSG = (PAT, BRACKETED, "own command line", "l-105", "done.marker")
MSG_VERB = (PAT, "own command line", "l-105", "done.marker")

BLOCK = [
    ("a count of the process listing for a name in its own command line", COUNT, "Bash", MSG),
    ("the count the RCA quotes, printed as a verdict", COUNT_VERDICT, "Bash", MSG),
    ("ps aux piped to a grep for a name in its own command line",
     PS_AUX + " | grep " + DQ + PAT + DQ, "Bash", MSG),
    ("ps -eo piped to a word-matching grep",
     PS + " -eo pid,cmd | grep -w " + DQ + PAT + DQ, "Bash", MSG),
    ("a count over the listing nested in a command substitution",
     "echo $(grep -c " + DQ + PAT + DQ + " /proc/*/cmdline)", "Bash", MSG),
    ("pgrep -f, the verb the guard already refuses", "pgrep -f " + DQ + PAT + DQ, "Bash", MSG_VERB),
]

ALLOW = [
    ("a count with a bracket class: the guard's own remedy, lines 100-102",
     "grep -c " + SQ + BRACKETED + SQ + " /proc/*/cmdline 2>/dev/null", "Bash"),
    ("ps piped to a bracketed grep", PS_AUX + " | grep " + SQ + BRACKETED + SQ, "Bash"),
    ("pgrep -f with a bracket class: the form the guard's message tells people to use",
     "pgrep -f " + SQ + BRACKETED + SQ, "Bash"),
    ("killing an explicit pid", "kill -9 2239619", "Bash"),
    ("waiting on an artifact", "until [ -s done.marker ]; do sleep 20; done", "Bash"),
    ("an ordinary commit", "git commit -q -F msg.txt", "Bash"),
    ("grepping source for the word pkill", "grep -rn " + SQ + "pkill" + SQ + " docs/", "Bash"),
    ("grepping source for the two-letter string ps",
     "grep -rn " + SQ + PS + SQ + " docs/", "Bash"),
    ("a note written by echo that spells out ps aux",
     'echo "' + PS + ' aux is not a liveness test" > notes.md', "Bash"),
    ("a doc written by a heredoc that spells the count out and is never run",
     "cat > notes.md <<'MD'\n" + COUNT + " is the shape\n" + "MD", "Bash"),
    ("a Write-tool payload is not this guard's population", COUNT, "Write"),
]


def says(r, parts):
    """Every part present, case-insensitively. 'probe.py' and '[p]robe.py' are BOTH required and
    neither contains the other, so the message has to show the pattern it extracted and its safe
    rendering. A message that says only 'don't grep a process listing' fails here."""
    low = r.stderr.lower()
    return all(p.lower() in low for p in parts)


def main():
    print("It refuses a count over a process listing that can see the command running it:")
    for label, cmd, tool, parts in BLOCK:
        r = run_full(cmd, tool)
        check(label, r.returncode, 2)
        check(label + " -> refusal names the pattern, its bracketed form, L-105 and the artifact",
              says(r, parts), True)
        check(label + " -> refusal opens with BLOCKED", r.stderr.startswith("BLOCKED"), True)

    print("\nAnd it leaves every legal form of the same work alone:")
    for label, cmd, tool in ALLOW:
        check(label, run(cmd, tool), 0)

    print("\nAnd the contract the existing suite pins is intact:")
    check("the guard under test is the file the brief names", HOOK.is_file(), True)
    check("a malformed payload never blocks",
          subprocess.run([sys.executable, str(HOOK)], input="not json",
                         capture_output=True, text=True, timeout=30).returncode, 0)

    print("\n" + str(PASSED) + " passed, " + str(FAILED) + " failed\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
