#!/usr/bin/env python3
"""Does this command's `pgrep -f` pattern match the command itself?

WHY THIS EXISTS
---------------
`L-105` says `pgrep -f` matches its own watcher. It is written down, it was quoted correctly
during the session that follows, and it was then walked into **three times in one evening**:

    until ! pgrep -f "ollama pull qwen3.6" >/dev/null; do sleep 15; done
    until ! pgrep -f "claude -p Please implement" >/dev/null; do sleep 10; done
    while pgrep -f "ollama pull qwen3.6" >/dev/null; do sleep 15; done

Each ran as `bash -c '<the whole text above>'`, so the pattern appears in the watcher's OWN
command line. `pgrep` finds itself, the condition never flips, and the loop runs forever. One
of them spun for 1h11m after the thing it was waiting for had finished, and they are the most
likely reason a scheduled wake-up had no idle session to fire into.

Knowing the rule was not enough. `L-115`'s lesson was that when a lesson describes a SHAPE you
should grep for the shape; this is that grep, for `L-105`.

WHAT IT CHECKS, AND WHAT IT CANNOT
----------------------------------
Given the text of a command, every `pgrep -f P` / `pkill -f P` is extracted and `P` is applied
as a regex to the command text itself. A hit means the process running the grep will match its
own command line.

`pkill -f` is reported as WORSE than `pgrep -f`: a self-matching `pgrep` hangs, a self-matching
`pkill` sends the signal to itself.

**And the trap is wider than `L-105` states.** It is not only long patterns in long loops.
Verified on 2026-08-26:

    $ pgrep -af zzznotathing        # a string nothing on the box is running
    147898 /bin/bash -c ...zzznotathing...
    exit 0

`pgrep` excludes its own pid and nothing else. When a harness runs every command as
`bash -c '<the whole text>'`, the pattern is sitting in the PARENT's command line, so
**any `pgrep -f <literal>` returns a match and exit 0 here — always.** It is not a
usable existence check in this environment at all. Use a pid you captured, a marker the
watched process writes, or `pgrep -f <pat> | grep -v $$`.

It cannot see a pattern built at runtime from a variable -- `pgrep -f "$NAME"` is reported as
UNKNOWN rather than passed, because "cannot tell" is not "fine" (`gpu_gate.py`'s rule).

Usage:
    selfmatch.py --cmd '<command text>'      one command, exit 1 if it self-matches
    selfmatch.py <file> [...]                scan files for inline watcher loops
    selfmatch.py --self-test
"""
import argparse
import re
import sys
from pathlib import Path

# `pgrep`/`pkill`, any flags containing f, then a quoted or bare pattern.
CALL = re.compile(r"""\b(pgrep|pkill)\s+((?:-\w+\s+)*)-(\w*f\w*)\s+(?:"([^"]*)"|'([^']*)'|(\S+))""")
VAR = re.compile(r"\$\{?\w+")

SAFE = "safe"
SELF = "self-match"
UNKNOWN = "unknown"


def findings(cmd):
    """[(verdict, tool, pattern, why)] for every -f grep in `cmd`."""
    out = []
    for m in CALL.finditer(cmd):
        tool = m.group(1)
        pat = m.group(4) or m.group(5) or m.group(6) or ""
        if not pat:
            continue
        if VAR.search(pat):
            out.append((UNKNOWN, tool, pat,
                        "the pattern is built from a variable, so it cannot be checked here. "
                        "Cannot tell is not the same as fine."))
            continue
        try:
            hit = re.search(pat, cmd)
        except re.error:
            # Not a valid regex; pgrep would reject it too, but a literal substring still self-matches.
            hit = pat in cmd
        if hit:
            worse = (" And it is `pkill`, so it signals ITSELF, not just hangs."
                     if tool == "pkill" else
                     " The condition never flips and the loop runs forever.")
            out.append((SELF, tool, pat,
                        f"{tool} -f {pat!r} matches this command's own text.{worse}"))
        else:
            out.append((SAFE, tool, pat, "does not match its own command line"))
    return out


def advise(pat):
    return (f"Fixes: match on something the watcher does not contain (a pid, or a marker the "
            f"watched process writes on exit); or exclude self with "
            f"`pgrep -f {pat!r} | grep -v $$`; or drop the pattern entirely and wait on the "
            f"process id you started, or on a sentinel line in its log.")


def report(label, cmd, quiet=False):
    bad = 0
    for verdict, tool, pat, why in findings(cmd):
        if verdict == SELF:
            bad += 1
            print(f"  [SELF-MATCH] {label}\n      {why}\n      {advise(pat)}")
        elif verdict == UNKNOWN:
            print(f"  [UNKNOWN]    {label}\n      {why}")
        elif not quiet:
            print(f"  [ok]         {label}: {tool} -f {pat!r} — {why}")
    return bad


# Loops are where a self-match is fatal rather than merely wrong.
LOOP = re.compile(r"\b(while|until)\b")


# A checker for a pattern must contain the pattern -- its docstring and its fixtures are made
# of the very thing it hunts. Named explicitly rather than excluded by a loose heuristic,
# because a blanket rule would also hide real bugs in these two files. Both are covered
# instead by `--self-test`, whose cases ARE those fixtures.
# check_stale_services.py joined this set on 2026-09-04 for the same reason: it grew a
# RUNTIME detector for the same bug -- one that reads /proc, because these loops are typed
# straight into a shell and never written to a tracked file, where this scanner cannot see
# them. Its docstring quotes `while pgrep -f foo.py` as the shape it hunts, and it runs
# `pgrep -f <pat>` deliberately to test whether a waiter matches itself. Both are the thing,
# not an instance of it. It is covered by scripts/tests/test_self_waiting.py, which spawns a
# REAL self-matching loop and asserts it is caught -- so excluding it here does not leave the
# behaviour unchecked, it moves the check to a place that can tell the two apart.
# guard_process_waits.py joined on 2026-09-07, the day it was written, for the same reason
# as the three above and with a pleasing symmetry: it is the PreToolUse hook that refuses
# these commands at the moment they are typed, so its docstring necessarily quotes the three
# that cost real time that day -- a search-by-name that reported a finished process as
# running, a liveness loop that waited ninety minutes on a zombie, and a kill-by-name that
# killed its own shell. This checker flagged all three inside the guard's own documentation.
# A guard for a pattern is not an instance of it. It is covered by
# scripts/tests/test_process_wait_guard.py, which feeds it real commands and asserts both
# what it must block and what it must let through.
# test_process_wait_selfcount.py joined on 2026-10-09 (t788, frozen that day): it is the suite
# for the count form of the same guard, so its cases ARE self-matching commands -- the very
# fixtures it feeds guard_process_waits.py and asserts are refused.
FIXTURE_FILES = {"selfmatch.py", "test_selfmatch.py",
                 "check_stale_services.py", "test_self_waiting.py",
                 "guard_process_waits.py", "test_process_wait_guard.py",
                 "test_process_wait_selfcount.py"}


def scan_file(p):
    if Path(p).name in FIXTURE_FILES:
        return 0
    text = Path(p).read_text(encoding="utf-8", errors="replace")
    bad = 0
    for i, line in enumerate(text.splitlines(), 1):
        # A comment describing the trap is not the trap. Found immediately: this checker's
        # first run flagged the comment in resume-coder-download.sh that explains why the
        # script no longer uses pgrep. Only whole-line comments are skipped -- a `#` mid-line
        # may sit inside a string, and guessing there would suppress real hits.
        if line.lstrip().startswith("#"):
            continue
        if not CALL.search(line):
            continue
        # A loop one-liner is the fatal shape: the whole line is the process's own cmdline.
        if LOOP.search(line):
            bad += report(f"{p}:{i}", line)
        else:
            for verdict, tool, pat, why in findings(line):
                if verdict == SELF:
                    bad += 1
                    print(f"  [SELF-MATCH] {p}:{i}\n      {why}\n      {advise(pat)}")
    return bad


SELF_TEST = [
    # (name, command, expect_self_match)  -- the first three are the real 2026-08-26 failures.
    ("the ollama pull watcher that spun 1h11m",
     'until ! pgrep -f "ollama pull qwen3.6" >/dev/null; do sleep 15; done', True),
    ("the claude -p watcher",
     'until ! pgrep -f "claude -p Please implement" >/dev/null 2>&1; do sleep 10; done', True),
    ("the persistent monitor form",
     'while pgrep -f "ollama pull qwen3.6" >/dev/null 2>&1; do sleep 15; done; echo done', True),
    ("a watcher whose pattern is NOT in its own text",
     'until [ -f /tmp/done ]; do sleep 5; done', False),
    # Not a false positive. Verified 2026-08-26: `pgrep -af zzznotathing` -- a string NOTHING
    # is running -- returns exit 0 and prints the wrapper shell. Under a harness that runs
    # every command as `bash -c '<whole text>'`, the pattern is always in the parent's cmdline
    # and pgrep excludes only its OWN pid. So a bare literal self-matches too.
    ("even a bare pgrep self-matches, because the wrapper shell holds the pattern",
     'pgrep -f nginx', True),
    ("pkill that would signal itself",
     'pkill -f "my-daemon" && echo "killed my-daemon"', True),
    ("a pattern that only appears in the echo, still self-matches",
     'pgrep -f worker >/dev/null || echo "no worker running"', True),
    ("bare -af flag form is recognised",
     'pgrep -af "ollama pull" | head', True),
    ("no pgrep at all",
     'ls -la /tmp && sleep 1', False),
    # scan_file skips whole-line comments; findings() deliberately does not, because a --cmd
    # string is executed verbatim and has no comments to speak of.
    ("a mid-line # is NOT treated as a comment, since it may be inside a string",
     'pgrep -f worker # worker check', True),
]


def self_test():
    print("selfmatch self-test — the first three are the real 2026-08-26 failures\n")
    bad = 0
    for name, cmd, want in SELF_TEST:
        got = any(v == SELF for v, *_ in findings(cmd))
        ok = got == want
        bad += not ok
        print(f"  [{'ok' if ok else 'FAIL'}]   {name}: {got} (want {want})")
    print(f"\n  {len(SELF_TEST) - bad} passed, {bad} failed")
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--cmd", help="check a single command string")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="only print problems")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    if a.cmd:
        bad = report("--cmd", a.cmd, quiet=a.quiet)
        if bad:
            print("\nRefusing: this command would watch itself (L-105).")
        return 1 if bad else 0
    if not a.files:
        ap.error("give --cmd, files, or --self-test")
    missing = [f for f in a.files if not Path(f).is_file()]
    scanned = [f for f in a.files if Path(f).is_file()]
    for f in missing:
        print(f"  [NOT SCANNED] {f}: file does not exist")
    if not scanned:
        print(f"\n  Refusing: 0 of {len(a.files)} file(s) could be examined. Cannot tell is not the same as fine.")
        return 2
    bad = sum(scan_file(f) for f in scanned)
    if not bad and not a.quiet:
        print(f"  no self-matching watcher in {len(scanned)} file(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
