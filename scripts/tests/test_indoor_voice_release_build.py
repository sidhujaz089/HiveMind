#!/usr/bin/env python3
"""t255 -- Indoor Voice needs a real release build type before F-Droid can carry it.

projects/indoor-voice/app/build.gradle.kts declares one build type, and it is the debug one:

    buildTypes {
        debug {
            // Debug signing only. No release keystore, no ProGuard/R8. Personal app.
            isMinifyEnabled = false
        }
    }

So `./gradlew assembleRelease` has no such task, and publish.py has nothing to publish: the
APK a debug build leaves on disk carries android:debuggable, `fdroid update` refuses to
suggest a debuggable version and writes CurrentVersionCode 2147483647 -- the 'nothing
suitable' sentinel -- so the client has no version to offer.

The contract is READ AT RUN TIME from the app that publishes correctly today, good-dictator-
app: the signing-config name is taken from the string its release build type passes to
signingConfigs.getByName(...), and the environment-variable names from the System.getenv(...)
calls inside its own signingConfigs block. The suite therefore cannot drift from the working
example and asserts no name I invented. It asserts the three distinct variables publish.py's
env() supplies -- GD_KEYSTORE, GD_KEYSTORE_PASS, GD_KEY_ALIAS -- not one repeated placeholder.

Red today: there is no `release {` block, no `signingConfigs {` block, no isMinifyEnabled on a
release type, no getByName(...) and no System.getenv(...) in the target file. Those assertions
run LAST, so the tail of stdout shows them.

No model, no server, no gradle, no writes: this reads two files in the repository. pytest is
not installed; run it as

    python3 scripts/tests/test_indoor_voice_release_build.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "projects" / "indoor-voice" / "app" / "build.gradle.kts"
REFERENCE = ROOT / "projects" / "good-dictator-app" / "app" / "build.gradle.kts"

_failures = []


def check(cond, msg):
    if cond:
        print("  ok   " + msg)
    else:
        print("  FAIL " + msg)
        _failures.append(msg)


def block(text, name):
    """Body of the first `name { ... }` block, outer braces excluded; '' when absent.

    Structural, not a word search: the target's own comment says 'No release keystore', so a
    substring test for 'release' is satisfied by a comment. Braces are counted so a nested
    block cannot be mistaken for the end of it.
    """
    m = re.search(re.escape(name) + r"\s*\{", text)
    if not m:
        return ""
    depth = 1
    j = m.end()
    while j < len(text) and depth:
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
        j += 1
    return text[m.end():j - 1]


def norm(s):
    return " ".join(s.split())


def main():
    print("t255: indoor-voice declares a release build type signed with the working key\n")
    for p in (TARGET, REFERENCE):
        if not p.exists():
            print("  FAIL missing file: %s" % (p,))
            return 1

    ref = REFERENCE.read_text(encoding="utf-8")
    tgt = TARGET.read_text(encoding="utf-8")

    # --- the contract, read from the app that publishes correctly today -----------
    ref_sign = block(ref, "signingConfigs")
    m = re.search(r'signingConfig\s*=\s*signingConfigs\.getByName\(\s*"([^"]+)"\s*\)', ref)
    name = m.group(1) if m else None
    envs = sorted(set(re.findall(r'System\.getenv\("([A-Z_]+)"\)', ref_sign)))
    check(name is not None, "the working app names the signing config to copy: %r" % (name,))
    check(len(envs) >= 3,
          "the working app reads its keystore from %d DISTINCT env vars: %s" % (len(envs), envs))

    build_types = block(tgt, "buildTypes")
    check(build_types.strip() != "", "the target file has a buildTypes block")
    debug = block(build_types, "debug")
    check(debug.strip() != "", "the existing debug build type is kept, so local debug builds work")
    check("signingConfig" not in debug,
          "the debug build type is NOT given the release signing key")
    check("isMinifyEnabled = false" in norm(debug),
          "the debug build type keeps the settings it has today (isMinifyEnabled = false)")

    # --- the defect: asserted LAST, so the runner's tail shows the failures -------
    rel = block(build_types, "release")
    check(rel.strip() != "",
          'buildTypes declares a "release { ... }" build type (today: debug is the only one)')
    check("isMinifyEnabled = false" in norm(rel),
          "the release build type sets isMinifyEnabled = false, as the working app does")
    check("isMinifyEnabled = true" not in norm(rel),
          "the release build type does NOT enable minification (no ProGuard/R8 on a personal app)")
    if name is not None:
        check('signingConfig = signingConfigs.getByName("%s")' % name in norm(rel),
              'the release build type signs with signingConfigs.getByName("%s")' % name)
    sign = block(tgt, "signingConfigs")
    check(sign.strip() != "", "the file declares a signingConfigs { } block for it to use")
    if name is not None:
        check('create("%s") {' % name in norm(sign),
              'signingConfigs creates the "%s" config that the release build type names' % name)
    for var in envs:
        check('System.getenv("%s")' % var in norm(sign),
              "the signing config reads %s from the environment, which publish.py's env() supplies" % var)
    check(re.search(r'storeFile\s*=\s*file\(\s*"', sign) is None,
          "no keystore path is committed to the repo (it is resolved from a variable, as in the working app)")

    print("\n%d failure(s)" % len(_failures))
    return 1 if _failures else 0


if __name__ == "__main__":
    sys.exit(main())
