#!/usr/bin/env python3
"""t255 -- publish.py must detect a missing release variant and REFUSE it.

publish.py:110-113 is supposed to catch gradle's "Task 'assembleRelease' not found" and stop.
Today it does neither of the two things it is for:

  * the test at line 110 lowercases the gradle output but not the needle it searches for, so
    the string "task 'assembleRelease' not found" (capital R) can never be found in a
    lowercased haystack -- the branch is unreachable, the debug fallback in its body never
    runs, and a project with no release variant dies at the generic raise below the block
    with a message that says only 'gradle exited 1';
  * what it should do -- refuse, in words that name the missing release build type -- is done
    nowhere in the file.

That matters because indoor-voice declares no release build type
(projects/indoor-voice/app/build.gradle.kts:19-24 has only `debug {`), and publish.py's own
comment above the block records the consequence of shipping a debug-built APK: F-Droid writes
CurrentVersionCode 2147483647, the 'nothing suitable' sentinel, and the client has no version
to offer.

Red today: build() DOES raise for such a project, so only the shape of the refusal is at issue
-- its first line is 'no-release-app: gradle exited 1', which names neither the missing task
nor the release build type. Every assertion below that fails on main is in the last function,
so a run that is green on main cannot hide behind the tail of stdout.

Offline, no live state: stub gradlew scripts inside a tempfile.TemporaryDirectory -- no model,
no server, no keystore, no writes outside the temp dir. pytest is not installed; run it as

    python3 scripts/tests/test_publish_no_debug_fallback.py
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PUBLISH = ROOT / "projects" / "app-repo" / "publish.py"

_failures = []


def check(cond, msg):
    if cond:
        print("  ok   " + msg)
    else:
        print("  FAIL " + msg)
        _failures.append(msg)


def load_publish():
    """Import projects/app-repo/publish.py by the path it really lives on. Importing it runs
    only path constants and the APPS dict; main() is guarded by __main__ (publish.py:422)."""
    spec = importlib.util.spec_from_file_location("publish_under_test", PUBLISH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Stub gradlew scripts. Each records every task it was asked for in a log beside itself and
# writes its APK by a path relative to its own directory. None of them reads an environment
# variable: publish.py's env() rewrites PATH, and a fixture that shares a channel with the
# machinery it models ends up measuring that machinery.
GRADLEW_NO_RELEASE = """#!/bin/sh
# A project declaring ONLY a debug build type, the state indoor-voice is in today.
cd "$(dirname "$0")"
echo "$1" >> gradle-calls.log
if [ "$1" = "assembleRelease" ]; then
  echo "FAILURE: Build failed with exception." >&2
  echo "* What went wrong: Task 'assembleRelease' not found in root project 'app'." >&2
  echo "* Try:" >&2
  echo "  Run gradlew tasks to get a list of available tasks." >&2
  echo "BUILD FAILED in 4s" >&2
  exit 1
fi
mkdir -p app/build/outputs/apk/debug
printf 'stub-debug-apk' > app/build/outputs/apk/debug/app-debug.apk
echo "1 actionable task: 1 executed"
exit 0
"""

GRADLEW_HAS_RELEASE = """#!/bin/sh
# A project that DOES declare a release build type, the shape good-dictator-app has. It
# refuses assembleDebug, so a debug fallback anywhere in build() is visible in its log.
cd "$(dirname "$0")"
echo "$1" >> gradle-calls.log
if [ "$1" = "assembleDebug" ]; then
  echo "FAILURE: Build failed with exception." >&2
  echo "* What went wrong: Task 'assembleDebug' not found in root project 'app'." >&2
  exit 1
fi
mkdir -p app/build/outputs/apk/release
printf 'stub-release-apk' > app/build/outputs/apk/release/app-release.apk
echo "1 actionable task: 1 executed"
exit 0
"""

GRADLEW_COMPILE_ERROR = """#!/bin/sh
# A project whose release build exists but FAILS to compile: a real build failure that the
# new refusal must not absorb. Says nothing about assembleRelease.
cd "$(dirname "$0")"
echo "$1" >> gradle-calls.log
echo "e: file:///app/src/main/java/com/jas/app/Foo.kt:12:5 Unresolved reference: bar" >&2
echo "BUILD FAILED in 7s" >&2
exit 1
"""

STUBS = {"no-release": GRADLEW_NO_RELEASE,
         "has-release": GRADLEW_HAS_RELEASE,
         "compile-error": GRADLEW_COMPILE_ERROR}


def call_build(mod, root, project, which):
    """Run publish.build(project) against a stub gradlew built inside `root`.

    build() resolves the project under the module-level WORKSPACE (publish.py:95), so
    WORKSPACE is pointed at `root` for the call and restored afterwards. Returns
    (result, raised, calls): calls is the list of gradle tasks the stub recorded.
    """
    proj = root / "projects" / project
    proj.mkdir(parents=True, exist_ok=True)
    gradlew = proj / "gradlew"
    gradlew.write_text(STUBS[which], encoding="utf-8")
    gradlew.chmod(0o755)
    saved = mod.WORKSPACE
    mod.WORKSPACE = root
    try:
        raised = None
        result = None
        try:
            result = mod.build(project)
        except mod.Failed as exc:
            raised = exc
        log = proj / "gradle-calls.log"
        calls = log.read_text(encoding="utf-8").split() if log.exists() else []
        return result, raised, calls
    finally:
        mod.WORKSPACE = saved


def test_a_release_build_still_publishes(mod, root):
    """Preservation: a project that HAS a release variant still builds and is returned.
    Green before and after the change; it exists to stop an implementation that makes
    build() refuse everything."""
    result, raised, calls = call_build(mod, root, "has-release-app", "has-release")
    print("  -- a project that does have a release variant:")
    check(raised is None, "it is not refused (observed: %r)" % (raised if raised is None else str(raised).splitlines()[0],))
    check(calls == ["assembleRelease"], "gradlew is asked for assembleRelease exactly once (asked for: %s)" % (calls,))
    apk = result[0] if isinstance(result, tuple) else result
    check(isinstance(apk, Path) and apk.exists(), "build() returns the APK it built: %r" % (apk,))
    check(str(apk).endswith(".apk") and (os.sep + "release" + os.sep) in str(apk),
          "that APK comes from apk/release/, never apk/debug/: %s" % (apk,))
    check(isinstance(result, tuple) and result[1] == 1,
          "the build reports the work it did (executed=%r)" % (result[1] if isinstance(result, tuple) else None,))


def test_an_unrelated_build_failure_still_reports_gradle(mod, root):
    """Preservation: a genuine compile failure must still be reported with gradle's exit
    code, not swallowed into the new refusal. Green before and after; it stops an
    implementation that raises the new message for every non-zero exit."""
    result, raised, calls = call_build(mod, root, "broken-app", "compile-error")
    print("  -- a project whose release build exists but does not compile:")
    check(raised is not None, "it is refused (observed: returned %r)" % (result,))
    msg = " ".join(str(raised).split()) if raised is not None else ""
    check("gradle exited 1" in msg,
          "the refusal carries gradle's exit code, as it does today: %r" % (msg[:120],))
    check("Unresolved reference" in msg,
          "the refusal carries gradle's own reason, not a generic one: %r" % (msg[:120],))


def test_a_missing_release_variant_is_refused_by_name(mod, root):
    """THE DEFECT. Asserted last, so the runner's tail -- what a reader and the lint quote
    -- shows these FAIL lines on main."""
    result, raised, calls = call_build(mod, root, "no-release-app", "no-release")
    msg = " ".join(str(raised).split()) if raised is not None else "build() returned %r" % (result,)
    first = str(raised).splitlines()[0] if raised is not None else "no exception; returned %r" % (result,)
    print("  -- a project whose gradlew has no assembleRelease task (its refusal today: %r)" % (first,))
    check(raised is not None, "build() raises Failed for it: %r" % (first,))
    check(calls == ["assembleRelease"],
          "gradlew is asked for assembleRelease exactly once (asked for: %s)" % (calls,))
    check("assembleDebug" not in calls,
          "gradlew is never asked for assembleDebug (asked for: %s)" % (calls,))
    check("assembleRelease" in first,
          "the refusal names the missing task in its FIRST line, not only in the gradle tail: %r" % (first,))
    check("exited" not in first,
          "the refusal is not the generic 'gradle exited N' line: %r" % (first,))
    check("no-release-app" in first,
          "the refusal names the project it refused in its first line: %r" % (first,))
    check("no assembleRelease task" in msg,
          "the refusal says the project has no assembleRelease task: %r" % (msg[:160],))
    check("release build type" in msg.lower(),
          "the refusal names the cause -- the app declares no release build type: %r" % (msg[:160],))
    check(result is None, "no APK is handed back when the release build cannot be made: %r" % (result,))
    debug_apk = root / "projects/no-release-app/app/build/outputs/apk/debug/app-debug.apk"
    check(not debug_apk.exists(),
          "no debug APK is built to publish in its place (exists: %s)" % (debug_apk.exists(),))


def main():
    print("t255: publish.py detects a missing release variant and refuses it\n")
    if not PUBLISH.exists():
        print("  FAIL publish.py not found at %s" % (PUBLISH,))
        return 1
    mod = load_publish()
    with tempfile.TemporaryDirectory(prefix="t255-publish-") as td:
        root = Path(td)
        test_a_release_build_still_publishes(mod, root)
        test_an_unrelated_build_failure_still_reports_gradle(mod, root)
        test_a_missing_release_variant_is_refused_by_name(mod, root)
    print("\n%d failure(s)" % len(_failures))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
