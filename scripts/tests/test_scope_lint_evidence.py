#!/usr/bin/env python3
"""t712 -- the scope lint's oracle evidence must name the checks that decided the run.

Two refusals on 2026-10-07, both class oracle-green on t255 chunk 0
(workspace/scope-defects/31dd9312dc31.json and workspace/scope-defects/67f40ace8f95.json),
returned to the Manager the same truncated tail -- the last six lines of the oracle's stdout:

  ok   the release variant is the one asked for (invocations: ['assembleRelease'])
  ok   the build reports the work it did (executed=1)
  0 failure(s)

Those are the guard checks of a project that does have a release build. The oracle's defect
case prints first, in its first test function, and is cut by the [-6:] at scope_lint.py:349.
The senior role's run_command is switched off, so that evidence string is the only view a
scope author gets of an oracle on main: from the tail above it cannot be learned which
assertion the change is supposed to break. The re-scope shipped the same green oracle eleven
minutes later and was refused for the same class.

This suite pins the replacement: a function that builds the evidence from the ok/FAIL lines
the oracle printed, names the checks the brief declared as the missing change when they pass
on main, says so when a red run is red for a reason the brief never declared, and keeps
today's last-six-lines tail for an oracle that printed no checks at all.

Red on main for one reason: scope_lint has no attribute 'oracle_evidence' and no EVIDENCE_MAX.
No model, no server, no subprocess call, no writes: strings in, a string out. pytest is not
installed -- run it directly:

    python3 scripts/tests/test_scope_lint_evidence.py
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scope_lint

_failures = []


def check(cond, msg):
    if cond:
        print("  ok   " + msg)
    else:
        print("  FAIL " + msg)
        _failures.append(msg)


def run_of(stdout="", stderr="", returncode=0):
    """A finished oracle run: oracle_evidence reads only .stdout and .stderr."""
    return subprocess.CompletedProcess([sys.executable, "-s", "x.py"], returncode, stdout, stderr)


def ok_line(label):
    return "  ok   " + label


def fail_line(label):
    return "  FAIL " + label


# The two labels the t255 scope itself declared as its missing change. Real strings, not
# placeholders: the evidence must echo the brief's own words.
REFUSED_LABEL = "build() REFUSES it (raises Failed) instead of building it as debug"
VARIANT_LABEL = "gradlew is never invoked with assembleDebug"

T255_RED_BECAUSE = (
    "So test_no_silent_debug_fallback fails on main at '" + REFUSED_LABEL + "' -- build() "
    "returns a path instead of raising -- and also at '" + VARIANT_LABEL + "' (it is invoked "
    "twice today). The second test function passes on main and after: it is the guard against "
    "making build() refuse everything."
)


def test_new_surface_exists():
    """The missing change, named plainly so the red reason is unambiguous."""
    check(hasattr(scope_lint, "oracle_evidence"),
          "scope_lint exposes oracle_evidence() to build the evidence from the checks the oracle printed")
    check(isinstance(getattr(scope_lint, "EVIDENCE_MAX", None), int),
          "scope_lint declares EVIDENCE_MAX, so the cap is a stated budget and not a magic slice")


def test_every_assertion_is_visible_in_the_evidence():
    """The defect: the last-six-lines tail drops the assertion that made the run red.

    Built like a real manager oracle: the defect case prints FIRST (two FAILs), then the
    guard case's seven ok checks, then the tally -- so the FAILs are nine lines from the end
    and no tail of six lines can reach them.
    """
    if not hasattr(scope_lint, "oracle_evidence"):
        check(False, "cannot check evidence content without oracle_evidence()")
        return
    body = "\n".join(
        [fail_line(REFUSED_LABEL),
         fail_line(VARIANT_LABEL + " (invocations: ['assembleRelease', 'assembleDebug'])")] +
        [ok_line(label) for label in (
            "the release variant is the one asked for (invocations: ['assembleRelease'])",
            "build() returns the built APK file: PosixPath('/tmp/t255-publish-x/app-release.apk')",
            "the APK comes from apk/release/, not apk/debug/",
            "the release path never runs assembleDebug",
            "the build reports the work it did (executed=1)",
            "the index lists the new versionCode",
            "the certificate matches the expected signing key")]
        + ["", "2 failure(s)"])
    ev = scope_lint.oracle_evidence(run_of(stdout=body, returncode=1))
    check(REFUSED_LABEL in ev,
          "the failing check that prints first is in the evidence -- the tail cut dropped it")
    check(VARIANT_LABEL in ev,
          "the second failing check is in the evidence too, not only the guard case's passes")
    check("2 failure(s)" not in ev,
          "the evidence is the assertions that decided the run, not the oracle's own tally line")


def test_a_green_run_names_the_checks_the_brief_declared():
    """The recurrence mechanism: a green run must say WHICH declared checks passed on main."""
    if not hasattr(scope_lint, "oracle_evidence"):
        check(False, "cannot check evidence content without oracle_evidence()")
        return
    body = "\n".join([
        ok_line(REFUSED_LABEL),
        ok_line(VARIANT_LABEL + " (asked for: ['assembleRelease'])"),
        ok_line("the release variant is the one asked for (invocations: ['assembleRelease'])"),
        ok_line("the build reports the work it did (executed=1)"),
        "", "0 failure(s)"])
    ev = scope_lint.oracle_evidence(run_of(stdout=body, returncode=0), T255_RED_BECAUSE)
    check("PASSES on main" in ev,
          "a green run is stated as green, which is the class the finding is filed under")
    check("PASSED on main" in ev and REFUSED_LABEL in ev and VARIANT_LABEL in ev,
          "the two checks the brief declared as the missing change are named as PASSED on main")
    check("the release variant is the one asked for" in ev,
          "the guard-case checks are shown as well, so the reader sees the whole run")
    check("FAIL" not in ev,
          "a green run's evidence claims no failure; the tail's 0 failure(s) was not a lie, it was useless")


def test_red_for_a_reason_the_brief_never_declared():
    """The half that stops shipping a green oracle: the declared reason is checked, not trusted.

    t255's declared reason was false -- its defect case passed on main because publish.py's
    run() helper raises Failed on any non-zero gradlew exit, upstream of the fallback the
    chunk edits. Here the run IS red, but for an import bug, and the evidence says so.
    """
    if not hasattr(scope_lint, "oracle_evidence"):
        check(False, "cannot check evidence content without oracle_evidence()")
        return
    body = "\n".join([fail_line("cannot import publish: No module named 'publish'"),
                      "", "1 failure(s)"])
    ev = scope_lint.oracle_evidence(run_of(stdout=body, returncode=1), T255_RED_BECAUSE)
    check("not for the reason the brief declares" in ev,
          "red for a reason the brief never declared is named as such in the evidence")
    check(REFUSED_LABEL in ev,
          "the check the brief declared is quoted back, so the author can see the mismatch")


def test_red_for_the_declared_reason_is_not_flagged():
    """The negative: a correct oracle must not be shouted at, or the finding gets routed away."""
    if not hasattr(scope_lint, "oracle_evidence"):
        check(False, "cannot check evidence content without oracle_evidence()")
        return
    body = "\n".join([fail_line(REFUSED_LABEL),
                      fail_line(VARIANT_LABEL + " (invocations: ['assembleDebug'])"),
                      "", "2 failure(s)"])
    ev = scope_lint.oracle_evidence(run_of(stdout=body, returncode=2), T255_RED_BECAUSE)
    check("not for the reason the brief declares" not in ev,
          "a run that IS red for the declared reason is not accused of being red for its own bug")


def test_a_run_that_prints_no_checks_keeps_the_old_tail():
    """Behaviour that must not change: a crashing oracle prints no ok/FAIL lines, and its
    traceback tail is the evidence today."""
    if not hasattr(scope_lint, "oracle_evidence"):
        check(False, "cannot check evidence content without oracle_evidence()")
        return
    body = ("Traceback (most recent call last):\n"
            '  File "scripts/tests/test_x.py", line 12\n'
            "    def broken(:\n"
            "            ^\n"
            "SyntaxError: invalid syntax\n")
    ev = scope_lint.oracle_evidence(run_of(stderr=body, returncode=1))
    check("SyntaxError" in ev, "an oracle that does not parse still yields its traceback tail")
    check("PASSES on main" not in ev, "a crash is never reported as a green oracle")


def test_the_cap_is_honest_and_ordered():
    """The evidence is a message, so it has a budget. A cut must be declared, and the check
    printed first -- the defect case, in every manager oracle -- must be the one that survives."""
    if not hasattr(scope_lint, "oracle_evidence"):
        check(False, "cannot check evidence content without oracle_evidence()")
        return
    filler = "x" * 60
    body = "\n".join([fail_line("the first check, which prints before all the others: " + filler)] +
                     [ok_line("guard check %03d " % i + filler) for i in range(200)] +
                     ["", "1 failure(s)"])
    ev = scope_lint.oracle_evidence(run_of(stdout=body, returncode=1))
    cap = getattr(scope_lint, "EVIDENCE_MAX", 3600)
    check(len(ev) <= cap + 80,
          "the evidence stays within the stated budget (EVIDENCE_MAX=%s, got %d chars)" % (cap, len(ev)))
    check("omitted" in ev,
          "the evidence says it was cut, so a reader cannot mistake a truncated message for the run")
    check("the first check, which prints before all the others" in ev,
          "ordering is preserved: the check printed first is the one a cap cuts last")


if __name__ == "__main__":
    print("t712: the scope lint's oracle evidence names the assertions that decided the run\n")
    test_new_surface_exists()
    test_every_assertion_is_visible_in_the_evidence()
    test_a_green_run_names_the_checks_the_brief_declared()
    test_red_for_a_reason_the_brief_never_declared()
    test_red_for_the_declared_reason_is_not_flagged()
    test_a_run_that_prints_no_checks_keeps_the_old_tail()
    test_the_cap_is_honest_and_ordered()
    print("\n%d failure(s)" % len(_failures))
    sys.exit(1 if _failures else 0)
