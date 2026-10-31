#!/usr/bin/env python3
"""Fail-first oracle for the refusal's evidence recorder (2026-10-24).

On 2026-10-24 eleven dispatches were REFUSED-TO-START on two suites that pass in a clean
sandbox, and nothing on disk said why: coder.py read the baseline as `already, _ = ...`,
printed the failing suites' NAMES, and then deleted the sandbox. The count was in the
message; the cause was in the value thrown away.

This asserts the recorder that keeps it: coder.record_baseline_output(outputs, dest) writes
each refusing suite's captured output, whole, to <dest>/<suite>.log -- the same names and
directory convention as scripts/run_tests.py's known-failures logs -- and returns the paths
so the refusal can print them.

Behaviour, not shape: what is written, that it is written whole, what an empty capture
becomes, and that a nested suite path does not change the file name.
"""
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "projects" / "local-inference"))

import coder  # noqa: E402

PASSED = FAILED = 0


def check(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def main():
    dest = Path(tempfile.mkdtemp(prefix="refusal-out-"))
    try:
        # LONGER THAN THE CAPTURE WINDOW, deliberately: coder keeps only the tail of each
        # suite's output, and a recorder that re-truncated it would still pass a short case.
        long_text = "".join(f"line {i} of the suite's own output\n" for i in range(200))
        assert len(long_text) > 5000, "the fixture must exceed any capture window"
        outputs = {
            "scripts/tests/test_manager_note.py": long_text,
            "scripts/tests/test_manager_outcomes.py": "boom: AssertionError at line 12\n",
        }

        kept = coder.record_baseline_output(outputs, dest)

        check("it returns one path per captured suite", len(kept), 2)
        check("the names follow run_tests.py's convention",
              sorted(Path(p).name for p in kept),
              ["test_manager_note.py.log", "test_manager_outcomes.py.log"])
        check("the paths it returns are the files on disk",
              all(Path(p).is_file() for p in kept), True)
        check("a suite's output is written WHOLE, not truncated",
              (dest / "test_manager_note.py.log").read_text(encoding="utf-8"), long_text)
        check("  and the second suite's output is its own",
              (dest / "test_manager_outcomes.py.log").read_text(encoding="utf-8"),
              "boom: AssertionError at line 12\n")

        # A zero-byte log reads as "nothing went wrong", the opposite of what it means.
        empty_dir = Path(tempfile.mkdtemp(prefix="refusal-out2-"))
        try:
            coder.record_baseline_output({"scripts/tests/test_quiet.py": ""}, empty_dir)
            text = (empty_dir / "test_quiet.py.log").read_text(encoding="utf-8")
            check("an empty capture becomes a marker, not a zero-byte file",
                  bool(text.strip()) and "no output" in text.lower(), True)
        finally:
            shutil.rmtree(empty_dir, ignore_errors=True)

        # A destination that does not exist yet must be created: the refusal can be the first
        # thing that ever writes there.
        fresh = Path(tempfile.mkdtemp(prefix="refusal-out3-")) / "nested" / "known-failures"
        try:
            coder.record_baseline_output({"scripts/tests/test_x.py": "x\n"}, fresh)
            check("a missing destination directory is created",
                  (fresh / "test_x.py.log").is_file(), True)
        finally:
            shutil.rmtree(fresh.parents[1], ignore_errors=True)
    finally:
        shutil.rmtree(dest, ignore_errors=True)

    print(f"{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
