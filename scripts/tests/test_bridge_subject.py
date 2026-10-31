#!/usr/bin/env python3
"""The bridge's generated commit subject, and the length limit it used to hit.

WHY. Every write Chat makes is committed through the same commit-msg hook Code
passes, which is what makes a bridge write attributable and revertable. The
subject of that commit is GENERATED from the path -- the caller cannot influence
it, on purpose. But the first version spent 36 of the hook's 72 characters on
fixed decoration and gave the rest to the whole path, so any path longer than 36
characters was unwritable through the bridge. Chat hit it on the first ordinary
dated filename it tried:

    workspace/capability-probe-2026-09-14.txt  ->  77 characters  ->  REJECTED

and the refusal blamed "the subject line" -- a message the caller never wrote,
for a cause it never named. Two different file CONTENTS produced the identical
error, which is what proved the subject was generated rather than read.

docs/CHECKS.md rule 1: the first assertion is that exact path. It fails against
the shipped version.

The shortening assertions matter as much as the length ones. A cap that silences
what it dropped is the defect being swept out of the coding path this week; a cap
that declares itself is a constraint. So: the full path must survive into the
body in EVERY case, and a shortened subject must be announced as shortened.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from workspace_mcp.core import SUBJECT_MAX, _subject   # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


CASES = [
    "workspace/probe.txt",
    "workspace/capability-probe-2026-09-14.txt",          # the reported failure
    "workspace/inbox/2026-09-14T225030Z--chat--a-long-descriptive-subject-line.md",
    "docs/" + "x" * 120 + ".md",                          # a name longer than the room
    "a.md",
]


def main():
    # ---- the reported case, named on its own so a regression is unmistakable
    sub, _ = _subject("workspace/capability-probe-2026-09-14.txt", "docs")
    report("the path Chat could not write now fits", len(sub) <= SUBJECT_MAX, True)

    # ---- no input produces an over-long subject, which is the hook's actual rule
    for rel in CASES:
        for verb in ("docs", "fix"):
            sub, shortened = _subject(rel, verb)
            report(f"<= {SUBJECT_MAX} chars: {verb} {rel[:34]}",
                   len(sub) <= SUBJECT_MAX, True)
            report(f"subject is one line: {rel[:34]}", "\n" in sub, False)
            report(f"conventional-commit prefix kept: {rel[:34]}",
                   sub.startswith(f"{verb}(workspace): "), True)
            # A subject that fits must NOT claim to have been shortened, or the
            # body carries a warning about nothing and the flag stops meaning
            # anything. Both directions, or "always True" scores green.
            fits_whole = len(f"{verb}(workspace): {rel}") <= SUBJECT_MAX
            report(f"shortened flag matches reality: {rel[:34]}",
                   shortened, not fits_whole)

    # ---- an unshortened subject still names the file, so the log stays readable
    sub, shortened = _subject("workspace/probe.txt", "docs")
    report("a short path is carried whole", "workspace/probe.txt" in sub, True)
    report("a short path is not flagged as shortened", shortened, False)

    # ---- a shortened one keeps the file name and says so with a marker
    sub, shortened = _subject("workspace/inbox/2026-09-14T225030Z--chat--a-long-"
                              "descriptive-subject-line.md", "docs")
    report("a long path is flagged as shortened", shortened, True)
    report("a shortened subject keeps the file name, not the directory",
           "inbox/" in sub, False)

    # ---- a name too long even alone is cut, and the cut is visible
    sub, shortened = _subject("docs/" + "x" * 120 + ".md", "docs")
    report("an over-long name is cut to the limit", len(sub), SUBJECT_MAX)
    report("the cut is marked in the subject", sub.endswith("…"), True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
