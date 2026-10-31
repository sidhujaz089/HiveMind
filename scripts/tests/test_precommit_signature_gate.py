#!/usr/bin/env python3
"""The contract for making the two known defect signatures UN-SHIPPABLE.

WHY THIS EXISTS. `scripts/harness_inventory.py` counts three defect signatures across 36
model-touching scripts and reports 13 quarantined. Counting is not preventing. Nothing stops
a thirty-seventh from being committed tomorrow, and the inventory would dutifully report 14.

Four of the nine outside reviewers of draft four said the same thing in different words.
Grok: "Treat the inventory as a CI gate, not a report." Z AI: "CI gate that fails on
re.search(r'\\{.*\\}'...) anywhere in the tree." Meta: "Inventory runs in CI and blocks merge
if greedy extractor or slice-into-record introduced." Qwen: "Write a linter or a pre-commit
hook that fails the build."

The two signatures worth blocking at commit time are the two that DESTROY EVIDENCE rather
than merely producing a wrong answer, because those are the ones you cannot diagnose after
the fact:

  * the greedy extractor -- `re.search(r"\\{.*\\}", text, re.S)`, first brace to last. It
    mis-parses three of the four shapes a model actually produces.
  * a fixed-width slice stored INTO a record -- `parse_error = answer[:400]`. Five of eight
    failing rows were exactly 400 bytes: the evidence needed to diagnose the failure was
    destroyed by the thing reporting it.

A slice into a `print()` or a log line is DISPLAY, not a record, and must not be blocked --
that distinction is already made correctly in harness_inventory.py and the hook must make it
too, or it will be bypassed with --no-verify within a week and be worth nothing.

THE ORACLE IS WRITTEN HERE AND THE IMPLEMENTATION IS DELEGATED. coder.py is given
scripts/hooks/pre-commit and never this file.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "hooks" / "pre-commit"
PASSED = FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got!r}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want!r}, got {got!r}")


def staged(filename, content):
    """Stage `content` as `filename` in a throwaway repo, run the hook, return its exit code.

    The hook reads `git diff --cached`, so it has to be a real repository with a real index.
    Nothing here touches the working repo.
    """
    d = Path(tempfile.mkdtemp())
    run = lambda *a: subprocess.run(a, cwd=d, capture_output=True, text=True)   # noqa: E731
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    p = d / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    run("git", "add", filename)
    r = subprocess.run(["sh", str(HOOK)], cwd=d, capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr)


if not HOOK.exists():
    print(f"  [BAD]  {HOOK} does not exist")
    print("\n0 passed, 1 failed\n")
    sys.exit(1)

print("The hook still blocks everything it blocked before -- this gate ADDS, it does not replace:")
rc, out = staged("leak.py", 'KEY = "sk-ant-' + "A" * 40 + '"\n')
check("a credential is still refused", rc, 1)
rc, out = staged("empty.md", "")
check("a zero-byte markdown file is still refused", rc, 1)
rc, out = staged("fine.py", "def f():\n    return 1\n")
check("an ordinary file still passes", rc, 0)

print("\nTHE GREEDY EXTRACTOR must not be committable:")
rc, out = staged("bad.py", 'import re\nm = re.search(r"\\{.*\\}", text, re.S)\n')
check("re.search(r'\\{.*\\}', text, re.S) is refused", rc, 1)
check("and the message names the signature", "greedy" in out.lower(), True)

rc, out = staged("bad2.py", "import re\nm = re.search(r'\\{.*\\}', answer, re.DOTALL)\n")
check("single quotes and re.DOTALL are refused too", rc, 1)

rc, out = staged("ok1.py", 'import re\nm = re.search(r"\\{[^{}]*\\}", text)\n')
check("a NON-greedy brace pattern still passes", rc, 0)

print("\nA FIXED-WIDTH SLICE STORED INTO A RECORD must not be committable:")
rc, out = staged("bad3.py", 'row["parse_error"] = answer[:400]\n')
check("assignment into a dict key is refused", rc, 1)
check("and the message says why truncating a record is different",
      "record" in out.lower() or "evidence" in out.lower(), True)

rc, out = staged("bad4.py", "row = {'raw': answer[:400]}\n")
check("a slice as a dict VALUE is refused", rc, 1)

print("\n...but DISPLAY is not a record, and blocking it would get the hook bypassed:")
rc, out = staged("ok2.py", 'print(f"    ERROR: {str(e)[:120]}")\n')
check("a slice inside print() passes", rc, 0)
rc, out = staged("ok3.py", 'log(f"{answer[:80]}")\n')
check("a slice inside a log line passes", rc, 0)
rc, out = staged("ok4.py", "head = answer[:400]\nprint(head)\n")
check("a slice into a plain local that is only printed passes", rc, 0)

print("\nThe gate applies to what is STAGED, not to what the repository already contains:")
rc, out = staged("docs/note.md", "We used to call re.search(r\"\\{.*\\}\", text) here.\n")
check("a non-Python file is not scanned at all", rc, 0)

# THIS IS THE CASE THAT MATTERS, AND IT USED TO BE TESTED WITH A .md FILE.
# The assertion above passes because the hook's `grep -E '\.py$'` filters the file out
# before the gate ever runs -- it proves the extension filter, not that prose is safe.
# Meanwhile the real repository refused eighteen files, scripts/jsonx.py among them: the
# module the whole extractor migration moved TO, whose docstring documents the greedy
# regex it replaces. A green suite over a case it never exercised is exactly the vacuous
# pass this project keeps finding (docs/CHECKS.md rule 1). The file must be .py.
print("\n...but PROSE ABOUT the defect, inside a .py file, is not the defect:")
rc, out = staged("prose.py",
                 'r\"\"\"This module replaces re.search(r\"\\{.*\\}\", text, re.S).\"\"\"\nimport json\n')
check("the greedy pattern in a .py DOCSTRING passes", rc, 0)
rc, out = staged("prose2.py", '# we used to write row[\"x\"] = raw[:200] here\nimport json\n')
check("a record slice in a .py COMMENT passes", rc, 0)
rc, out = staged("real.py", 'import json\nrow = {}\nrow[\"parse_error\"] = raw[:200]\n')
check("...while the same slice in real CODE is still refused", rc, 1)

print(f"\n{PASSED} passed, {FAILED} failed\n")
sys.exit(1 if FAILED else 0)
