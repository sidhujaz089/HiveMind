#!/usr/bin/env python3
"""The overnight driver must not destroy the work it exists to collect.

WHY THIS SUITE EXISTS
---------------------
`probe_overnight.py` became the most load-bearing script in the repository on 2026-08-27 --
it drives an 87-hour unattended run with nobody watching -- and it had never been made to
fail. I verified its preservation logic by hand and it worked, which is exactly what "it
passed" meant for the four vacuous checks fixed that same morning (L-136 to L-138).

THE DEFECT IT GUARDS
--------------------
`refresh_env3()` resets env3 to origin/main so each pass probes current code. `reset --hard`
discards changes to TRACKED files -- which is precisely what a fix is. The run's whole
purpose is that the model fixes what it finds and a human reviews those fixes; six pass
boundaries would have wiped six passes of work in silence, and the review would have found
an empty tree and concluded the model did nothing.

Untracked files survive a reset. Patches do not. That asymmetry is the trap.
"""
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "projects" / "local-inference"))
import probe_overnight as po  # noqa: E402

PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    ok = got == want
    print(f"  [{'ok' if ok else 'BAD'}]   {label}: {got if ok else f'expected {want}, got {got}'}")
    if ok:
        PASSED += 1
    else:
        FAILED += 1


def refused(x):
    """True when the driver REFUSED to proceed rather than pressing on."""
    return bool(x)


def git(repo, *a):
    return subprocess.run(("git", "-C", str(repo)) + a, capture_output=True, text=True)


def make_origin_and_clone():
    """An origin with one commit, and a clone of it standing in for env3."""
    root = Path(tempfile.mkdtemp())
    origin, clone = root / "origin", root / "env3"
    origin.mkdir()
    git(origin, "init", "-q", "--initial-branch=main")
    git(origin, "config", "user.email", "t@t")
    git(origin, "config", "user.name", "t")
    (origin / "code.py").write_text("x = 1\n")
    git(origin, "add", "-A")
    git(origin, "commit", "-qm", "base")
    subprocess.run(("git", "clone", "-q", str(origin), str(clone)), capture_output=True)
    git(clone, "config", "user.email", "t@t")
    git(clone, "config", "user.name", "t")
    return root, origin, clone


def main():
    real_env3 = po.ENV3
    root, origin, clone = make_origin_and_clone()
    try:
        po.ENV3 = clone

        # These replace assertions written for the PREVIOUS architecture -- per-pass branches
        # plus `reset --hard` -- which preserved the model's fixes and simultaneously deleted
        # them from the tree it probes. Jas caught that by asking whether the fixes were ever
        # applied. Testing the old behaviour now would be testing a defect.
        print("The model's work is attributed and marked unreviewed:")
        (clone / "code.py").write_text("x = 2   # the model's fix\n")
        po.refresh_env3(3, "some-surface")
        msg = git(clone, "log", "-1", "--format=%B").stdout
        report("the commit names the model", "ollama/qwen3.8" in msg, True)
        report("  and says it is unreviewed", "NOT reviewed" in msg, True)
        report("  and the work branch is checked out",
               git(clone, "branch", "--show-current").stdout.strip(), "probe-work")

        print("\nThe model's fixes are IN the tree it probes, not beside it:")
        (clone / "code.py").write_text("x = 2   # fix one\n")
        po.refresh_env3(1, "first")
        report("fix one survives into the tree", (clone / "code.py").read_text(),
               "x = 2   # fix one\n")
        (clone / "second.py").write_text("y = 1   # fix two\n")
        po.refresh_env3(2, "second")
        report("  and fix two joins it", (clone / "second.py").exists(), True)
        report("  while fix one is still there", (clone / "code.py").read_text(),
               "x = 2   # fix one\n")
        carried = git(clone, "rev-list", "--count", "origin/main..HEAD").stdout.strip()
        report("  both carried as commits on the work branch", int(carried) >= 2, True)

        # Upstream must still arrive -- carrying fixes must not mean going stale.
        (Path(str(origin)) / "upstream.py").write_text("z = 9\n")
        git(origin, "add", "-A")
        git(origin, "commit", "-qm", "upstream change")
        po.refresh_env3(3, "third")
        report("  and NEW upstream code still arrives", (clone / "upstream.py").exists(), True)

        print("\nA conflict fails safely rather than half-applying:")
        (Path(str(origin)) / "code.py").write_text("x = 99  # human changed the same line\n")
        git(origin, "add", "-A")
        git(origin, "commit", "-qm", "human edit colliding with the model")
        po.refresh_env3(4, "conflicting")
        conflicted = "probe-conflict" in git(clone, "branch", "--list").stdout
        report("the conflicting work is set aside on a branch", conflicted, True)
        mid = git(clone, "status", "--porcelain").stdout
        report("  and no half-finished rebase is left behind", "UU" in mid, False)

        print("\nThe fixes get OUT of the disposable sandbox:")
        real_archive = po.ARCHIVE
        po.ARCHIVE = Path(tempfile.mkdtemp()) / "archive"
        try:
            (clone / "code.py").write_text("x = 3   # another fix\n")
            po.refresh_env3(7, "archive-surface")
            bundles = list(po.ARCHIVE.glob("*.bundle")) if po.ARCHIVE.exists() else []
            report("a bundle is written outside env3", refused(bundles), True)
            if bundles:
                v = subprocess.run(("git", "-C", str(clone), "bundle", "verify",
                                    str(bundles[0])), capture_output=True, text=True)
                report("  and it records a COMPLETE history",
                       "complete history" in v.stdout, True)
                report("  and it lives outside the sandbox",
                       str(clone) in str(bundles[0]), False)
        finally:
            po.ARCHIVE = real_archive

        print("\nA clean tree makes no branch:")
        before = git(clone, "branch", "--list", "probe-fixes/*").stdout.count("probe-fixes")
        po.refresh_env3(4, "another-surface")
        after = git(clone, "branch", "--list", "probe-fixes/*").stdout.count("probe-fixes")
        report("no empty branch for a pass that changed nothing", after, before)

        print("\nThe gate's verdict reaches the exit code:")
        psrc = (Path(__file__).resolve().parents[2] / "projects" / "local-inference"
                / "probe.py").read_text()
        report("a failed gate returns non-zero", refused("return 0 if ok else" in psrc), True)
        report("  rather than returning 0 regardless",
               "f\"  findings: {len(findings)}\")\n" in psrc and "return 0\n\n\nif __name__" in psrc,
               False)

        print("\nA SKIPPED mutation is not a passing one:")
        import re as _re
        gsrc = (Path(__file__).resolve().parents[2] / "projects" / "local-inference"
            / "probe.py").read_text()
        parses = _re.search(r"skipped\)\?", gsrc) is not None
        report("the gate parses the skipped count at all", refused(parses), True)
        report("  and refuses on a non-zero skip",
           refused("if skipped:" in gsrc and "SKIPPED" in gsrc), True)
        report("  saying the property is no longer proven",
           refused("no longer proven" in gsrc), True)

        print("\nThe known-findings list does not claim they are all fixed:")
        report("it stops asserting FIXED", "have since been FIXED" in gsrc, False)
        report("  and says a still-present defect IS a finding",
           refused("that IS a finding" in gsrc), True)

        print("\nThe disk guard refuses rather than filling the disk:")
        # Drive the real constant, not a copy of the rule.
        src = (Path(__file__).resolve().parents[2] / "projects" / "local-inference"
               / "probe_overnight.py").read_text()
        report("a floor exists and is checked before a pass",
               refused("free_gb < 40" in src and "shutil.disk_usage" in src), True)
        report("  and it BREAKS rather than warning and continuing",
               refused("Refusing to start another pass" in src), True)

        print("\nA dead pass does not end the run:")
        report("failures are counted, not fatal", refused("fails += 1" in src), True)
        report("  and the loop continues", refused("time.sleep(60)" in src), True)
        report("  but a persistent failure stops it", refused("fails >= 12" in src), True)
    finally:
        po.ENV3 = real_env3
        subprocess.run(("rm", "-rf", str(root)), capture_output=True)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
