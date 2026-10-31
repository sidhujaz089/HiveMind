#!/usr/bin/env python3
"""Refuse a backgrounded loop whose exit test can match the loop's own command line.

Class (workspace/rca/5f8f9b97e198.md, session 6cc34e3b, 2026-09-28): a watcher loop greps a
process listing for a string that appears in its own command line, so the search always finds
the waiting shell and the loop's exit condition can never become false.

THE PROPERTY BEING PINNED, NOT A VERB. scripts/hooks/guard_process_waits.py:41 matches the
literal shape "pgrep -f" / "pkill -f", and strip_heredocs (line 47) deletes every heredoc body
before the text is matched. Three shapes of this class arrive at a gate whose population cannot
contain them: a watcher written into a script by the command that launches it; a loop whose exit
test scans a process listing (/proc/*/cmdline) instead of naming pgrep; and any of the above
typed through the workspace's own shell tool, which main (line 69) does not accept at all. The
rule to add, and the only fact that separates the class from ordinary work:

    the loop's exit selector carries a pattern, and that pattern, INTERPRETED AS A REGULAR
    EXPRESSION, matches the text of the command launching the loop.

"As a regular expression" is what keeps the bracket class legal: [o]llama pull qwen3.6 matches
"ollama pull qwen3.6", and a command that writes the bracketed form contains no literal
"ollama pull qwen3.6" to match. A plain substring test would block the remedy the guard's own
message tells people to use (line 101), so the bracketed cases below are the fixtures that make a
substring test fail. The two heredoc cases in the ALLOW list are the fixtures that make a
block-everything build fail: a command that writes the loop into a file and does not run it is
documentation, not a watcher.

scripts/tests/test_process_wait_guard.py is this guard's other half. It is NOT touched by this
file: it pins the four commands typed on 2026-09-07, and every selector it blocks appears
literally in its own text, so a rule keyed on self-match keeps all of them blocked.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "scripts" / "hooks" / "guard_process_waits.py"
PAT = "ollama pull qwen3.6"
BRACKETED = "[o]llama pull qwen3.6"
SQ, DQ = chr(39), chr(34)
PASSED = FAILED = 0


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


def count_cmd(p):
    """until [ "$(grep -c "p" /proc/*/cmdline ...)" -gt 0 ] -- the form the RCA quotes, no pgrep."""
    return ("nohup bash -c " + SQ + "until [ " + DQ + "$(grep -c " + DQ + p
            + DQ + '" /proc/*/cmdline 2>/dev/null | paste -sd+ - | bc)" -gt 0 ]; '
            "do sleep 30; done" + SQ + " >/dev/null 2>&1 &")


def scan_cmd(p):
    return ("nohup bash -c " + SQ + "until grep -q " + DQ + p + DQ
            + " /proc/*/cmdline 2>/dev/null; do sleep 30; done" + SQ + " >/dev/null 2>&1 &")


def write_and_launch(script, body, launch):
    """The creation endpoint's real shape: the command writes the watcher and starts it."""
    return "cat > " + script + " <<'EOF'\n" + body + "\nEOF\n" + launch


def write_only(script, body):
    """The same text, written into a file the command never runs -- data, not a watcher."""
    return "cat > " + script + " <<'EOF'\n" + body + "\nEOF"


# BLOCK: the loop's exit selector can see the loop itself. Every one of these is exit 0 today.
BLOCK = [
    ("a watcher written into a script by the command that launches it",
     write_and_launch("w.sh", 'until ! pgrep -f "' + PAT + '"; do sleep 30; done', "bash w.sh &"),
     "Bash"),
    ("the same watcher written with a quoted heredoc and started with nohup sh",
     write_and_launch("w2.sh", 'while pgrep -f "' + PAT + '" >/dev/null; do sleep 30; done',
                      "nohup sh w2.sh >/dev/null 2>&1 &"), "Bash"),
    ("a loop whose exit test counts the process listing for its own text",
     count_cmd(PAT), "Bash"),
    ("a loop whose exit test scans the process listing for its own text",
     scan_cmd(PAT), "Bash"),
    ("the typed until-loop arriving on the workspace's own shell tool",
     'until ! pgrep -f "' + PAT + '"; do sleep 30; done', "mcp__local__bash"),
]

# ALLOW: the same work, phrased so the selector cannot match the command launching it.
ALLOW = [
    ("a pgrep selector bracketed so it cannot match its own text",
     "while pgrep -f '" + BRACKETED + "' >/dev/null; do sleep 30; done", "Bash"),
    ("a loop counting the listing with a bracketed selector",
     count_cmd(BRACKETED), "Bash"),
    ("a loop scanning the listing with a bracketed selector",
     scan_cmd(BRACKETED), "Bash"),
    ("a loop waiting on an artifact", "until [ -s done.marker ]; do sleep 20; done", "Bash"),
    ("a doc written by a heredoc that spells the loop out and is never run",
     write_only("notes.md", 'until ! pgrep -f "' + PAT + '"; do sleep 30; done is the shape'),
     "Bash"),
    ("a script written by a heredoc that the command does not run",
     write_only("w3.sh", 'while pgrep -f "' + PAT + '" >/dev/null; do sleep 30; done'), "Bash"),
    ("killing an explicit pid", "kill -9 2239619", "Bash"),
    # The sample is assembled so the literal never appears in this file (check_workspace's watcher-loop
    # scan reads every script's text and would flag it as a real self-matching loop).
    ("a Write-tool payload is not this guard's population", "pgrep" + " -f anything", "Write"),
]


def main():
    print("It refuses a loop whose exit selector can see the loop itself:")
    for label, cmd, tool in BLOCK:
        check(label, run(cmd, tool), 2)

    print("\nAnd it leaves every legal form of the same work alone:")
    for label, cmd, tool in ALLOW:
        check(label, run(cmd, tool), 0)

    print("\nAnd every refusal says why THIS form is blocked, and what to do instead:")
    for label, cmd, tool in BLOCK:
        r = run_full(cmd, tool)
        low = r.stderr.lower()
        check(label + " -> says BLOCKED, cites L-105, offers the artifact, names the self-match",
              (r.returncode == 2, r.stderr.startswith("BLOCKED"), "l-105" in low,
               "done.marker" in low, "own command line" in low),
              (True, True, True, True, True))

    print("\nAnd the contract the existing suite pins is intact:")
    check("the guard under test is the file the brief names", HOOK.is_file(), True)
    check("a malformed payload never blocks",
          subprocess.run([sys.executable, str(HOOK)], input="not json",
                         capture_output=True, text=True, timeout=30).returncode, 0)

    print("\n" + str(PASSED) + " passed, " + str(FAILED) + " failed\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
