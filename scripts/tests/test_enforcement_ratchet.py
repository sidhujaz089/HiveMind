#!/usr/bin/env python3
"""The ratchet must FAIL when an unenforced lesson is added, and when a guard disappears.

A ratchet that cannot fail is a number in a file. Each case below builds the mistake in a
throwaway copy of the repository's inputs and confirms the checker refuses it.

THIS SUITE TESTS THE MECHANISM, NOT THE REPOSITORY'S CURRENT COMPLIANCE, and the distinction was
paid for on 2026-09-15. It used to open with "the repository as it stands passes", so a single
lesson written with no guard -- a documentation shortfall, committed by nobody, sitting in the
working tree -- failed this suite, which made coder.py's sandbox baseline untrustworthy, which
made it refuse EVERY dispatch. The build pipeline stopped for hours over a missing ENFORCEMENT
line. The ratchet is right and the coupling was an accident.

So each case now supplies its own ceiling, computed to make its own baseline compliant, and none
of them reads the live ceiling file. Whether the repository is over its ceiling TODAY is a
question about state, and state is what check_workspace.py reports -- at session start and
session end, where a person sees it -- under "Enforcement ratchet". That check still fails.
Tests prove the tool works; checks report what the repository is.
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check_enforcement.py"


def sandbox():
    """A throwaway repository the checker cannot tell from the real one (t326).

    The fixtures used to be written into the REAL docs/LESSONS.md and enforcement-ceiling.json and
    restored in a finally -- and on 2026-09-15 the kernel OOM-killed the suite between the two: the
    fixture survived, the real checker failed, and coder.py refused every dispatch. A finally cannot
    survive SIGKILL, so the fixture must never land in a tracked file. Here scripts/ and docs/ are
    COPIED (the checker is a runtime copy, and roots off its own __file__), the ceiling is copied,
    and everything else is a symlink so every guard a lesson names still resolves. A kill leaves
    nothing but this temp dir. Verified 2026-09-30: audit() gives the same result here as in the
    real tree."""
    root = Path(tempfile.mkdtemp(prefix="enf-ratchet-"))
    for e in REPO.iterdir():
        if e.name not in (".git", "scripts", "docs", "workspace", "chat"):
            (root / e.name).symlink_to(e)
    shutil.copytree(REPO / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO / "docs", root / "docs")
    (root / "workspace").mkdir()
    for e in (REPO / "workspace").iterdir():
        if e.name != "enforcement-ceiling.json":
            (root / "workspace" / e.name).symlink_to(e)
    shutil.copy2(REPO / "workspace" / "enforcement-ceiling.json", root / "workspace" / "enforcement-ceiling.json")
    return root


def run_against(lessons_text=None, ceiling_obj=None, extra=None, args=()):
    """Run the checker in a fresh sandbox with LESSONS.md / the ceiling replaced there -- never in
    the real tree. `extra` = {relative path: text} written into the sandbox first. Returns
    (rc, output, sandbox root); the caller may read the sandbox, then it is removed."""
    root = sandbox()
    try:
        if lessons_text is not None:
            (root / "docs" / "LESSONS.md").write_text(lessons_text)
        if ceiling_obj is not None:
            (root / "workspace" / "enforcement-ceiling.json").write_text(json.dumps(ceiling_obj, indent=1) + "\n")
        for rel, text in (extra or {}).items():
            (root / rel).write_text(text)
        r = subprocess.run([sys.executable, str(root / "scripts" / "check_enforcement.py"), *args],
                           capture_output=True, text=True, timeout=180)
        after = json.loads((root / "workspace" / "enforcement-ceiling.json").read_text())
        return r.returncode, r.stdout + r.stderr, after
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    fails = []
    base = (REPO / "docs" / "LESSONS.md").read_text()

    # THE BASELINE IS SYNTHETIC. The ceiling is set to whatever the repository's unenforced count
    # actually is, so the starting point is compliant BY CONSTRUCTION and every case below fails
    # for the reason it names rather than inheriting a failure that was already there.
    sys.path.insert(0, str(REPO / "scripts"))
    import check_enforcement
    n = len(check_enforcement.audit()[0])
    at_cap = {"_what": "fixture written by test_enforcement_ratchet.py", "max_unenforced": n,
              "set": "fixture", "lowered_from": n}

    rc, out, _ = run_against(ceiling_obj=at_cap)
    print(f"  [{'ok' if rc == 0 else 'BAD'}]    a compliant baseline passes, ceiling {n} "
          f"(rc={rc})")
    if rc != 0:
        fails.append(f"the checker rejects a baseline built to satisfy it, so nothing below "
                     f"means anything:\n{out[-400:]}")

    # 1. ADDING AN UNENFORCED LESSON MUST BREAK IT. This is the case the ratchet exists for.
    extra = base + ("\n\n**L-999 [VERIFIED 2026-09-03]** A lesson written with no mechanical "
                    "guard at all.\n\nENFORCEMENT: none — judgement.\n")
    rc, out, _ = run_against(lessons_text=extra, ceiling_obj=at_cap)
    caught = rc != 0 and "ceiling" in out.lower()
    print(f"  [{'ok' if caught else 'BAD'}]    one more ENFORCEMENT: none breaks the ratchet "
          f"(rc={rc})")
    if not caught:
        fails.append("adding an unenforced lesson did not fail the check -- the ratchet does "
                     "not ratchet")

    # 2. AN ENFORCEMENT NAMING A FILE THAT IS GONE MUST BREAK IT. Worse than none: it reads
    #    as covered. exam_bank.py promised an endurance.py that never existed.
    gone = base + ("\n\n**L-998 [VERIFIED 2026-09-03]** A lesson pointing at a guard that is "
                   "not there.\n\nENFORCEMENT: `scripts/this_was_never_written.py` runs every "
                   "session.\n")
    rc, out, _ = run_against(lessons_text=gone, ceiling_obj=at_cap)
    caught = rc != 0 and "does not exist" in out
    print(f"  [{'ok' if caught else 'BAD'}]    an enforcement naming a missing file is caught "
          f"(rc={rc})")
    if not caught:
        fails.append("a lesson naming a nonexistent guard passed -- a dangling enforcement "
                     "reads as coverage")

    # 3. AN ENFORCEMENT NAMING A TEST NOTHING RUNS MUST BREAK IT.
    orphan_name = "test_nothing_invokes_this.py"
    txt = base + (f"\n\n**L-997 [VERIFIED 2026-09-03]** A lesson guarded by a test nothing "
                  f"invokes.\n\nENFORCEMENT: `scripts/tests/{orphan_name}`.\n")
    rc, out, _ = run_against(lessons_text=txt, ceiling_obj=at_cap,
                             extra={f"scripts/tests/{orphan_name}": "# fixture written by test_enforcement_ratchet.py\n"})
    caught = rc != 0 and "nothing runs it" in out
    print(f"  [{'ok' if caught else 'BAD'}]    an enforcement naming an unrun test is "
          f"caught (rc={rc})")
    if not caught:
        fails.append("a lesson guarded by a test nothing invokes passed")

    # 4. THE CEILING MAY NOT BE RAISED BY THE TOOL ITSELF. The invariant is RELATIVE, not the
    #    literal 26 this once asserted: the ceiling file says it "may go DOWN and never up
    #    without a deliberate edit", and check_enforcement's own refusal tells you to "raise
    #    the number deliberately and say why". A hardcoded number failed every such raise and
    #    called it the tool misbehaving.
    #    Asserting `after <= before` on the live ceiling would pass no matter what the tool
    #    did to it, because the tool's refusal keeps them equal -- a case that cannot fail.
    #    So put the ceiling BELOW the real count, where raising it is the tempting thing to
    #    do, and require a refusal that leaves the number alone.
    floor = 1  # below any real unenforced count, so --ratchet would have to RAISE to "fix" it
    live = json.loads((REPO / "workspace" / "enforcement-ceiling.json").read_text())
    rc, out, after_obj = run_against(ceiling_obj={**live, "max_unenforced": floor}, args=("--ratchet",))
    after = after_obj["max_unenforced"]
    refused = rc != 0 and after == floor
    print(f"  [{'ok' if refused else 'BAD'}]    --ratchet refuses to raise a ceiling below the "
          f"count (rc={rc}, ceiling still {after})")
    if not refused:
        fails.append(f"--ratchet moved the ceiling {floor} -> {after} instead of refusing; it "
                     f"must only lower it")

    for x in fails:
        print("FAIL:", x)
    print("test_enforcement_ratchet:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
