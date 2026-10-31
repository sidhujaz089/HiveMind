#!/usr/bin/env python3
"""t670 -- session_start.py must carry the index of indexes: each finder and when
to reach for it. Red on main because session_start.FINDERS and
session_start._render_finder_index do not exist yet; green once
scripts/session_start.py defines both, with the five task-named finders each
carrying a distinct when-to-reach."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import session_start

# The five finders the task names, in the exact short form the section must use.
EXPECTED_FINDERS = {
    "memory.py recall",
    "symindex.py find",
    "skills.py find",
    "PROJECT-INDEX.md",
    "docs/skills/INDEX.md",
}


def test_finders_index_present():
    assert hasattr(session_start, "FINDERS"), "session_start.FINDERS is not defined"
    assert isinstance(session_start.FINDERS, list), "FINDERS must be a list of (finder, when)"
    assert len(session_start.FINDERS) == 5, f"expected 5 finders, got {len(session_start.FINDERS)}"


def test_each_task_named_finder_is_named():
    named = {finder for finder, _when in session_start.FINDERS}
    missing = EXPECTED_FINDERS - named
    assert not missing, f"finders not named in the index: {sorted(missing)}"


def test_each_has_a_distinct_when_to_reach():
    whens = [when for _finder, when in session_start.FINDERS]
    assert all(isinstance(w, str) and w.strip() for w in whens), (
        "every finder needs a non-empty when-to-reach"
    )
    assert len(set(whens)) == len(whens), (
        "when-to-reach must be distinct, not one string repeated five times"
    )


def test_section_renders_all_five():
    lines = session_start._render_finder_index()
    joined = "\n".join(lines)
    for name in EXPECTED_FINDERS:
        assert name in joined, f"rendered section is missing {name}"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
        except Exception as e:  # the red reason is the missing change, not a crash
            failed += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"{failed} test(s) failed")
        sys.exit(1)
    print(f"ok: {len(fns)} test(s) passed")
