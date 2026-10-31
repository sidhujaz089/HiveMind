#!/usr/bin/env python3
"""Fixtures for merge_accounting.py, on a real git repository.

WHY BOTH DIRECTIONS, and why t310 said so explicitly: "a patch matching its brief
passes SILENTLY, and a patch carrying an unrequested second change is FLAGGED and
names that change. Without the first half, a checker that flags everything scores
green."

That is not a hypothetical failure mode. A scope checker that returns "unaccounted"
for every hunk catches coder/brief-8f25f27d perfectly, catches every future case
perfectly, and is worthless -- it would be switched off inside a week. The clean
delivery passing in silence is the assertion that makes the other one mean
anything.

THE CASE REBUILT HERE is 8f25f27d in miniature, because it is the one that got
through: TWO changes in ONE file the brief named, where the brief asked for one.
Stage A (is the file named?) passes it. Only the hunk stage catches it. A fixture
built from a change in an unnamed file would have let a stage-A-only
implementation score green, which is the mistake this fixture is shaped to avoid.

The model is injected. A fixture that needs a 27B model to be resident is a
fixture that gets skipped, and a skipped check is not a check -- and the judge's
CONTRACT (what it does with yes, with no, and with an answer it cannot read) is
what is being pinned here, not the model's taste.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import merge_accounting as MA   # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {str(got)[:70]}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {str(got)[:70]}")


def sh(*a, cwd):
    return subprocess.run(a, cwd=str(cwd), capture_output=True, text=True)


BRIEF = """Widen the value side of the truncation signature in gate.py.

The pattern currently matches a value of 10 characters or more. Change it to
match a value of 4 or more, so a short truncated value is still caught.

Do not change anything else in gate.py.
"""

ORIGINAL = '''def signature(line):
    """Does this line look like a truncated record?"""
    if re.match(r"^\\w+ = .{10,}$", line):
        return True
    if re.match(r"^\\d{3,}$", line):
        return True
    return False
'''

# What the brief asked for: the value side, 10 -> 4.
ASKED_ONLY = ORIGINAL.replace('.{10,}', '.{4,}')

# 8f25f27d in miniature: the asked-for change PLUS a second, unrequested one --
# the digit floor narrowed from 3 to 5. Same file, and the brief names that file.
ASKED_PLUS_EXTRA = ASKED_ONLY.replace(r'^\d{3,}$', r'^\d{5,}$')


def build(content_after):
    tmp = Path(tempfile.mkdtemp())
    sh("git", "init", "-q", "-b", "main", ".", cwd=tmp)
    sh("git", "config", "user.email", "t@t", cwd=tmp)
    sh("git", "config", "user.name", "t", cwd=tmp)
    (tmp / "gate.py").write_text("import re\n\n\n" + ORIGINAL)
    (tmp / "untouched.py").write_text("x = 1\n")
    sh("git", "add", "-A", cwd=tmp)
    sh("git", "commit", "-qm", "base", cwd=tmp)
    base = sh("git", "rev-parse", "HEAD", cwd=tmp).stdout.strip()
    sh("git", "checkout", "-qb", "delivery", cwd=tmp)
    (tmp / "gate.py").write_text("import re\n\n\n" + content_after)
    sh("git", "add", "-A", cwd=tmp)
    sh("git", "commit", "-qm", "delivery", cwd=tmp)
    return tmp, base


def with_repo(tmp, fn):
    real = MA.REPO
    MA.REPO = tmp
    try:
        return fn()
    finally:
        MA.REPO = real


def main():
    # A judge standing in for the model: it knows the brief asked to widen the
    # value side, and nothing else.
    def ask(prompt, model):
        hunk = prompt.split("=== THE HUNK ===", 1)[-1]
        if ".{4,}" in hunk or ".{10,}" in hunk:
            return "ACCOUNTED: yes\nWHY: widens the value side, which the brief asked for."
        return ("ACCOUNTED: no\nWHY: narrows the digit floor from 3 to 5; "
                "the brief never mentions the digit pattern.")

    # ---- 1. THE CLEAN DELIVERY MUST PASS, SILENTLY
    tmp, base = build(ASKED_ONLY)
    try:
        f = with_repo(tmp, lambda: MA.account(base, "delivery", BRIEF, ask=ask))
        bad = [x for x in f if not x["accounted"]]
        report("a delivery matching its brief has findings at all", len(f) > 0, True)
        report("a delivery matching its brief is fully accounted", len(bad), 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 2. THE EXTRA CHANGE MUST BE CAUGHT, AND NAMED
    tmp, base = build(ASKED_PLUS_EXTRA)
    try:
        f = with_repo(tmp, lambda: MA.account(base, "delivery", BRIEF, ask=ask))
        bad = [x for x in f if not x["accounted"]]
        report("the unrequested second change is flagged", len(bad), 1)
        report("it is flagged at hunk level, not file level",
               bad and bad[0]["stage"], "hunk")
        report("the finding names the file", bad and bad[0]["file"], "gate.py")
        report("the finding says what it does that nobody asked for",
               bool(bad) and "digit" in bad[0]["why"], True)
        report("the asked-for change is NOT flagged alongside it",
               any(x["accounted"] for x in f), True)

        # The trap this fixture exists for: stage A alone passes this delivery.
        f_a = with_repo(tmp, lambda: MA.account(base, "delivery", BRIEF, ask=ask,
                                                skip_hunks=True))
        report("stage A alone would have missed it (which is why B exists)",
               [x for x in f_a if not x["accounted"]], [])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 3. A FILE THE BRIEF NEVER NAMES IS CAUGHT WITHOUT A MODEL
    tmp, base = build(ASKED_ONLY)
    try:
        (tmp / "untouched.py").write_text("x = 2\n")
        sh("git", "add", "-A", cwd=tmp)
        sh("git", "commit", "-qm", "sneak", cwd=tmp)
        f = with_repo(tmp, lambda: MA.account(base, "delivery", BRIEF, ask=ask))
        bad = [x for x in f if not x["accounted"]]
        report("a file the brief never names is unaccounted",
               [x["file"] for x in bad], ["untouched.py"])
        report("and it is caught mechanically, with no model",
               bad and bad[0]["stage"], "file")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # ---- 4. AN UNREADABLE VERDICT BLOCKS. A judge that passes on confusion is not one.
    ok, why = MA.judge(BRIEF, "gate.py", "@@", "m", ask=lambda p, m: "I'm not sure really")
    report("an unreadable verdict is treated as unaccounted", ok, False)
    report("and the record says the verdict was unreadable", "unreadable" in why, True)
    ok, _ = MA.judge(BRIEF, "gate.py", "@@", "m", ask=lambda p, m: "")
    report("an empty reply is treated as unaccounted", ok, False)
    ok, _ = MA.judge(BRIEF, "gate.py", "@@", "m",
                     ask=lambda p, m: "ACCOUNTED: yes\nWHY: fine")
    report("a clean yes is honoured", ok, True)

    # t563: a line whose only change is indentation is found without a model
    report("a re-indented line is found",
           MA.reindented("-    x = 1\n+        x = 1\n-y\n+z"), ["x = 1"])
    report("a real edit is not a re-indent", MA.reindented("-a = 1\n+a = 2"), [])
    report("blank lines are ignored", MA.reindented("-    \n+        "), [])

    # t311: a branch whose brief file is gone is accounted against the brief it carries
    real = MA.git
    try:
        MA.git = lambda *a: (0, "feat(brief-x): change\n\n--- BRIEF AS DISPATCHED ---\n"
                                "TASK: t1\nEdit gate.py\n--- END BRIEF ---\n\nAgent: ollama/m\n", "")
        report("the brief is read back from the commit", MA.brief_from_commit("coder/brief-x"),
               "TASK: t1\nEdit gate.py")
        MA.git = lambda *a: (0, "feat: no brief kept\n\nAgent: x\n", "")
        report("an old branch without one says None", MA.brief_from_commit("coder/brief-y"), None)
    finally:
        MA.git = real
    import sys as _s
    _s.path.insert(0, str(ROOT / "projects" / "local-inference"))
    import coder as _C
    src = (ROOT / "projects" / "local-inference" / "coder.py").read_text()
    report("coder writes the brief into the branch commit",
           "write_branch(label, obj[\"ops\"], a.model, pre, brief=brief)" in src
           and _C.BRIEF_MARK == "--- BRIEF AS DISPATCHED ---", True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
