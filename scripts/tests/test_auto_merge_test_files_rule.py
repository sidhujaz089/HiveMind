#!/usr/bin/env python3
"""Phase 0b-2: auto-merge takes no file under scripts/tests/ except the brief's own NEW test, and the
shape check runs on every changed file, not only files[0].

THE HOLE. With a FILES line, files_allowed() refused only the oracle by path; any OTHER existing test
named on the FILES line went through, so a build could weaken a neighbouring suite and merge unread.
And judge() ran code_shape.regressions on files[0] alone: a regression in the second named file was
never looked at.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import auto_merge as am  # noqa: E402

PASSED = FAILED = 0


def check(name, ok, detail=""):
    global PASSED, FAILED
    PASSED += bool(ok)
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        {detail}"))


def main():
    oracle = "scripts/tests/test_x.py"
    existed = {"scripts/one.py", "scripts/tests/test_other.py", oracle}.__contains__
    named = ["scripts/one.py", "scripts/tests/test_other.py"]
    ok, why = am.files_allowed(["scripts/one.py", "scripts/tests/test_other.py"], oracle, named, existed)
    check("an EXISTING test named on the FILES line is refused", not ok and "test" in why, why)
    named = ["scripts/one.py", "scripts/tests/test_new.py"]
    ok, why = am.files_allowed(["scripts/one.py", "scripts/tests/test_new.py"], oracle, named, existed)
    check("the brief's own NEW test is allowed", ok, why)
    ok, why = am.files_allowed(["scripts/tests/test_other.py"], oracle, [], existed)
    check("one-file rule: a test is still refused", not ok, why)
    ok, why = am.files_allowed(["scripts/one.py"], oracle, ["scripts/one.py"], existed)
    check("a plain named source file is still allowed", ok, why)
    ok, why = am.files_allowed(["scripts/one.py", oracle], oracle, ["scripts/one.py", oracle], existed)
    check("the oracle is still refused", not ok and "oracle" in why, why)
    ok, why = am.files_allowed(["scripts/one.py", "scripts/tests/test_other.py"], oracle,
                               ["scripts/one.py", "scripts/tests/test_other.py"])
    check("without an existence check, ANY named test is refused (fail closed)", not ok, why)

    src = (REPO / "scripts" / "auto_merge.py").read_text()
    body = src[src.index("def judge("):src.index("def merge(")]
    check("judge() no longer runs the shape check on files[0] alone",
          "show(branch, path), show(\"main\", path)" not in body)
    check("judge() runs code_shape.regressions inside a loop over the files",
          "for f in files" in body and "regressions(" in body[body.index("for f in files"):])
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
