#!/usr/bin/env python3
"""Invariants for the app pipeline: signing key, catalogue, release notes, services.

    python3 scripts/check_apps.py
    python3 scripts/check_apps.py --self-test

WHY THESE FOUR
--------------
The app pipeline has one catastrophic failure and three annoying ones, and all four are
silent until the moment they cost something.

  * **An APK signed with the wrong key.** Android identifies an app by package + signing key,
    so a mismatch is not a bad update, it is an uninstallable one -- the only route is
    uninstall, which deletes the app's data. For good-dictator-app that data is the recording
    corpus, which is not reproducible. This already happened once: superbutton's installed
    copy is debug-signed and its repo copy is not.
  * **The keystore missing or replaced.** Same consequence, arriving later and harder to
    diagnose.
  * **A published version with no release notes.** Jas tests from the checklist; a version
    without one ships with nothing to test against, and he finds out by opening the page.
  * **A dead service.** The repo and the feedback page are how apps and notes move at all.

Every check reads the artefact rather than trusting that the tool which made it succeeded --
`publish.py` verifies at publish time, and this verifies that what is on disk *now* still
holds. Those are different questions, and only the second survives someone copying a file in
by hand.
"""

import json
import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pysource  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_REPO = Path.home() / "app-repo"
KEYSTORE = Path.home() / ".secrets" / "jas-apps.keystore"
BUILD_TOOLS = Path.home() / "android-toolchain" / "sdk" / "build-tools" / "36.0.0"
PUBLISH_PY = REPO_ROOT / "projects" / "app-repo" / "publish.py"
SERVICES = ("app-repo.service", "app-feedback.service")

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"


def expected_fingerprint():
    """Read the constant out of publish.py rather than duplicating it here.

    L-046: state a fact once. Two copies of a fingerprint is two chances to update one and
    not the other, and the failure would be a check that agrees with a key nobody uses.
    """
    v = pysource.constant(PUBLISH_PY, "EXPECTED_CERT_SHA256")
    if not v:
        raise RuntimeError("could not find EXPECTED_CERT_SHA256 in publish.py")
    return v


def apk_cert(apk):
    r = subprocess.run(
        [str(BUILD_TOOLS / "apksigner"), "verify", "--print-certs", str(apk)],
        capture_output=True, text=True, timeout=180,
        env={"PATH": f"{Path.home()}/android-toolchain/jdk-17.0.20+8/bin:/usr/bin:/bin",
             "JAVA_HOME": f"{Path.home()}/android-toolchain/jdk-17.0.20+8",
             "QEMU_LD_PREFIX": f"{Path.home()}/android-toolchain/x86_64-sysroot"})
    m = re.search(r"certificate SHA-256 digest: ([0-9a-f]+)", r.stdout)
    return m.group(1) if m else None


def checks():
    """Yield (status, headline, detail). Detail is empty on PASS."""
    if not APP_REPO.is_dir():
        yield SKIP, "The app catalogue is signed with the one key", \
            f"{APP_REPO} does not exist on this machine.\nNothing was checked -- not a pass."
        return

    want = expected_fingerprint()

    # --- the keystore itself ---------------------------------------------------------
    if not KEYSTORE.exists():
        yield FAIL, "The app signing key exists", \
            f"{KEYSTORE} is missing. Without it NO app can ever be updated again -- only\n" \
            "uninstalled, taking its data. A copy is on the USB at secrets-backup/."
    else:
        mode = oct(KEYSTORE.stat().st_mode)[-3:]
        if mode != "600":
            yield WARN, "The app signing key is private", f"{KEYSTORE} is mode {mode}, not 600."
        else:
            yield PASS, "The app signing key exists and is mode 600", ""

    # --- every published APK ---------------------------------------------------------
    apks = sorted((APP_REPO / "repo").glob("*.apk"))
    if not apks:
        yield WARN, "The app catalogue has APKs in it", "repo/ contains no .apk files."
    elif not (BUILD_TOOLS / "apksigner").exists():
        yield SKIP, "The app catalogue is signed with the one key", \
            "apksigner not present; the Android toolchain is not installed here.\n" \
            "Nothing was checked -- not a pass."
    else:
        wrong = []
        for a in apks:
            got = apk_cert(a)
            if got != want:
                wrong.append(f"{a.name}: {got or 'unreadable'}")
        if wrong:
            yield FAIL, "The app catalogue is signed with the one key", \
                "\n".join(wrong) + f"\n\nexpected {want}\nAn app signed with another key " \
                "cannot be updated over, only reinstalled."
        else:
            yield PASS, f"All {len(apks)} published APK(s) carry the expected signing key", ""

    # --- release notes for every published version -----------------------------------
    missing = []
    for a in apks:
        m = re.match(r"(com\.jas\.[a-z]+)_(.+)\.apk$", a.name)
        if not m:
            continue
        pkg = m.group(1)
        notes = list((APP_REPO / "metadata" / pkg / "en-US" / "changelogs").glob("*.txt")) \
            if (APP_REPO / "metadata" / pkg).is_dir() else []
        if not notes:
            missing.append(f"{pkg}: no changelog in the repo metadata")
    if missing:
        yield WARN, "Every published app has release notes", "\n".join(missing) + \
            "\n\nJas tests from the checklist in those notes; a version without one ships\n" \
            "with nothing to test against."
    elif apks:
        yield PASS, "Every published app has release notes", ""

    # --- do the notes actually REACH the phone? --------------------------------------
    #
    # The check above asks whether a changelog exists in the repo. It has always passed.
    # Jas still saw no release notes on his phone, twice, because fdroidserver only attaches
    # `whatsNew` to the index when the app has Builds entries:
    #
    #     if len(builds) > 0 and version["versionCode"] == builds[-1]["versionCode"]:
    #
    # A binary repo has none, so a perfectly good changelog was dropped from the index in
    # silence. THIS is the property the client consumes, and nothing was checking it -- the
    # third time (L-083, L-084) a green catalogue check sat next to a phone that disagreed.
    index = APP_REPO / "repo" / "index-v2.json"
    if index.is_file():
        try:
            idx = json.loads(index.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            yield WARN, "The index carries release notes", f"index-v2.json unreadable: {e}"
            idx = None
        if idx is not None:
            silent = []
            examined = 0
            for pkg, entry in sorted(idx.get("packages", {}).items()):
                versions = entry.get("versions", {})
                if not versions:
                    continue
                examined += 1
                newest = max(versions.values(),
                             key=lambda v: v.get("manifest", {}).get("versionCode", 0))
                # A present-but-empty whatsNew renders nothing on the phone, which is the
                # same failure as a missing one (Jas saw no release notes twice). Check the
                # value, not the key: fdroidserver writes the key even when the value is "".
                if not str(newest.get("whatsNew", "")).strip():
                    code = newest.get("manifest", {}).get("versionCode", "?")
                    silent.append(
                        f"{pkg}: versionCode {code} has an empty whatsNew in index-v2.json -- "
                        f"the phone will show no release notes")
            if silent:
                yield WARN, "The index carries release notes to the phone", \
                    "\n".join(silent) + \
                    "\n\nUsually a missing `Builds:` entry in metadata/<pkg>.yml. publish.py\n" \
                    "writes one; a hand-edited yml may have lost it."
            elif not examined:
                # NOTHING WAS EXAMINED, WHICH IS NOT A PASS. An index with no packages -- or
                # whose packages carry no versions -- walked straight past the loop leaving
                # `silent` empty, and this reported that the index carries release notes to
                # the phone. The check that exists BECAUSE Jas twice saw no release notes
                # could report success about an index carrying nothing at all.
                #
                # The recurring shape in this repository: a search that reported ok having
                # scanned no files, a census unable to see the failure it existed for. Found
                # by the env3 probe on the checks-that-cannot-fail pass, and it is the fourth
                # time the app catalogue has been green next to a phone that disagreed
                # (L-083, L-084).
                yield WARN, "The index carries release notes to the phone", \
                    "index-v2.json lists NO package with any version, so nothing was " \
                    "examined.\nThat is not a pass: the phone would be offered an empty " \
                    "catalogue and this\ncheck would have said the release notes were fine."
            else:
                yield PASS, "The index carries release notes to the phone", \
                    f"{examined} package(s) examined"

    # --- the services ----------------------------------------------------------------
    dead = []
    for svc in SERVICES:
        r = subprocess.run(["systemctl", "--user", "is-active", svc],
                           capture_output=True, text=True, timeout=30)
        if r.stdout.strip() != "active":
            dead.append(f"{svc}: {r.stdout.strip() or 'unknown'}")
    if dead:
        yield WARN, "The app services are running", "\n".join(dead) + \
            "\n\nWithout these, apps cannot be installed and notes cannot be sent."
    else:
        yield PASS, "The app repo and feedback services are running", ""


def self_test():
    """The refusals matter. Each case must produce a FAIL or WARN, not a PASS."""
    import tempfile, shutil
    global APP_REPO, KEYSTORE
    print("self-test -- each case must be caught\n")
    real_repo, real_ks = APP_REPO, KEYSTORE
    passed = failed = 0

    def expect(label, status_wanted, headline_substr):
        nonlocal passed, failed
        got = [(s, h) for s, h, _ in checks() if headline_substr in h]
        ok = any(s == status_wanted for s, _ in got)
        print(f"  [{'ok' if ok else 'BAD'}]   {label}: {[s for s, _ in got] or 'no such check'}")
        if ok:
            passed += 1
        else:
            failed += 1

    tmp = Path(tempfile.mkdtemp())
    try:
        # A missing keystore must FAIL, never pass quietly.
        (tmp / "repo").mkdir()
        APP_REPO = tmp
        KEYSTORE = tmp / "does-not-exist.keystore"
        expect("a missing signing key", FAIL, "signing key exists")
        expect("an empty catalogue", WARN, "has APKs in it")

        # AN INDEX THAT CARRIES NOTHING MUST NOT PASS. This reported PASS -- "the index
        # carries release notes to the phone" -- for an index with no packages at all,
        # because the loop that builds `silent` never ran and an empty `silent` meant
        # success. The check exists because Jas twice saw no release notes on his phone.
        import json as _json
        APP_REPO = tmp
        (tmp / "repo" / "index-v2.json").write_text(_json.dumps({"packages": {}}))
        expect("an index with no packages", WARN, "release notes to the phone")
        (tmp / "repo" / "index-v2.json").write_text(
            _json.dumps({"packages": {"com.x": {"versions": {}}}}))
        expect("packages carrying no versions", WARN, "release notes to the phone")
        # A present-but-empty whatsNew renders nothing on the phone -- the same failure
        # as a missing one. fdroidserver writes the key even when the value is "".
        (tmp / "repo" / "index-v2.json").write_text(
            _json.dumps({"packages": {"com.x": {"versions": {
                "1": {"manifest": {"versionCode": 1}, "whatsNew": ""}}}}}))
        expect("an empty whatsNew", WARN, "release notes to the phone")
        (tmp / "repo" / "index-v2.json").unlink()

        # An absent repo must SKIP -- explicitly not a pass.
        APP_REPO = tmp / "nowhere"
        expect("no catalogue at all", SKIP, "signed with the one key")
    finally:
        APP_REPO, KEYSTORE = real_repo, real_ks
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n  {passed} caught, {failed} missed")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    print("App pipeline\n")
    worst = 0
    for status, headline, detail in checks():
        colour = {"PASS": "32", "WARN": "33", "FAIL": "31", "SKIP": "90"}[status]
        print(f"  \033[{colour}m[{status}]\033[0m {headline}")
        for line in detail.splitlines():
            print(f"         {line}")
        worst = max(worst, {"PASS": 0, "SKIP": 0, "WARN": 1, "FAIL": 2}[status])
    return worst if worst == 2 else 0


if __name__ == "__main__":
    sys.exit(main())
