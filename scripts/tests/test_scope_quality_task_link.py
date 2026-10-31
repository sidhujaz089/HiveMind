#!/usr/bin/env python3
"""Register 'task-link' as a known defect class that is NOT a scope class.

The task-link commit gate (an enrolled build chunk whose brief TASK line names no task on disk)
is recorded with class 'oracle-gate', a SCOPE class (scope_quality.py:57). A scope class sets
scope_fault (record, line 143) and increments the model's scope_defects (score, line 211), so
task-link commit-gate refusals count against the model and pile onto this oracle-gate task
(route, line 298) instead of being attributed to the bridge's commit gate. Registering 'task-link'
in OTHER_CLASSES (not SCOPE_CLASSES) makes it recordable via the CLI (line 335), printed
'(not the scope)' in the report (line 236), and stops it lowering the model's first-pass yield.

Pure: imports the real module and asserts constant membership. No subprocess, no urlopen, no
/tmp, no model call, no live state touched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import scope_quality


def test_task_link_is_registered():
    # the CLI must accept the class (record/CLI validates SCOPE_CLASSES | OTHER_CLASSES, line 335)
    assert "task-link" in (scope_quality.SCOPE_CLASSES | scope_quality.OTHER_CLASSES)


def test_task_link_is_not_a_scope_class():
    # a task-link gate refusal must NOT count against the model (record line 143, score line 211)
    assert "task-link" not in scope_quality.SCOPE_CLASSES
    assert "task-link" in scope_quality.OTHER_CLASSES


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("  PASS  " + fn.__name__)
        except AssertionError as e:
            failed += 1
            print("  FAIL  " + fn.__name__ + (": " + str(e) if str(e) else ""))
    print(str(len(fns) - failed) + " passed, " + str(failed) + " failed")
    sys.exit(1 if failed else 0)
