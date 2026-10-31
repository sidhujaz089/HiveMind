#!/usr/bin/env python3
"""The app catalogue checks must refuse, not reassure.

check_apps.py carries its own self_test() whose contract is "each case must produce a FAIL
or WARN, not a PASS". Nothing in scripts/tests/ drove it, so it ran only when a person
remembered to pass --self-test -- which is the same shape as the defects it guards against.

THE CASE THAT PROMPTED THIS (2026-08-27, env3 probe, checks-that-cannot-fail pass)
---------------------------------------------------------------------------------
"The index carries release notes to the phone" reported PASS for an index-v2.json with no
packages in it. The loop that collects silent packages never ran, the empty result read as
success, and the check that exists BECAUSE Jas twice saw no release notes on his phone would
have said the release notes were fine about a catalogue carrying nothing.

Fourth time the catalogue has been green next to a phone that disagreed (L-083, L-084).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_apps


def refused(status):
    """True when a check REFUSED — anything that is not a PASS."""
    return status != "PASS"


def main():
    # Drive one case directly as well as through self_test(), so this suite states the
    # property itself rather than delegating all of it: an index carrying nothing must not
    # report that release notes reach the phone.
    import json
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    (tmp / "repo").mkdir()
    (tmp / "repo" / "index-v2.json").write_text(json.dumps({"packages": {}}))
    check_apps.APP_REPO = tmp
    got = [s for s, h, _ in check_apps.checks() if "release notes to the phone" in h]
    empty_ok = bool(got) and all(refused(s) for s in got)
    print(f"  [{'ok' if empty_ok else 'FAIL'}]   an empty index is refused(: {got}")

    rc = check_apps.self_test()
    ok = rc == 0 and empty_ok
    print(f"\n  [{'ok' if ok else 'FAIL'}]   check_apps self-test: every case refused")
    if not ok:
        print("FAIL: a case that must produce FAIL or WARN reported PASS")
    print("test_check_apps:", "FAILED" if not ok else "ok")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
