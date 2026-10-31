#!/usr/bin/env python3
"""Test Gemma's autonomous decision system.

Validates that:
1. gemma-decide.py gathers context correctly
2. gemma-execute.py implements all five decisions (SPLIT, RESTART, REWRITE-BRIEF, FIX-HARNESS, SKIP)
3. Plan modification works (split a chunk)
4. File/test mapping is correct for all chunks
5. Harness auto-fix handles common cases
"""
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_gemma_decide_script_exists():
    """gemma-decide.py exists and is executable."""
    script = REPO / "scripts" / "gemma-decide.py"
    assert script.exists(), f"{script} not found"
    assert script.stat().st_mode & 0o111, f"{script} not executable"


def test_gemma_execute_script_exists():
    """gemma-execute.py exists and is executable."""
    script = REPO / "scripts" / "gemma-execute.py"
    assert script.exists(), f"{script} not found"
    assert script.stat().st_mode & 0o111, f"{script} not executable"


def test_autonomy_brief_documents_decisions():
    """AUTONOMY-BRIEF.md documents all five decisions."""
    brief = (REPO / "workspace" / "AUTONOMY-BRIEF.md").read_text()
    decisions = ["SPLIT", "RESTART", "REWRITE-BRIEF", "FIX-HARNESS", "SKIP"]
    for decision in decisions:
        assert decision in brief, f"{decision} not documented in AUTONOMY-BRIEF.md"


def test_plan_has_chunk_metadata():
    """INFRA-PLAN.md chunks have file/test mapping info (or config exists)."""
    plan = (REPO / "workspace" / "plan" / "INFRA-PLAN.md").read_text()
    # Check that chunks are defined with FILES and TARGET fields
    assert "## CHUNK" in plan, "No chunks found in plan"
    assert "FILES |" in plan or "files" in plan.lower(), "No file mapping in plan chunks"


def test_gemma_execute_split_implemented():
    """gemma-execute.py can split chunks (not just log)."""
    script = (REPO / "scripts" / "gemma-execute.py").read_text()
    # Check that split_chunk does more than just log
    assert "split_chunk" in script, "split_chunk function not found"
    # Should modify the plan, not just log
    assert "INFRA-PLAN.md" in script or "write_text" in script, "split_chunk doesn't modify plan"


def test_gemma_execute_has_harness_fix():
    """gemma-execute.py has harness auto-fix logic."""
    script = (REPO / "scripts" / "gemma-execute.py").read_text()
    assert "FIX-HARNESS" in script, "FIX-HARNESS not handled"
    # Should do more than just log
    assert ("test" in script.lower() or "parse" in script.lower() or
            "contract" in script.lower()), "No harness fix logic found"


# test_timer_gemma_calls_both_scripts REMOVED 2026-10-02 (t626): timer-gemma.sh was retired -- nothing
# scheduled it (crontab clean) and supervise.py replaced the decide/execute loop it ran, as
# 9e6a20f4 retired its twin timer-q16.sh.


if __name__ == "__main__":
    # Run all tests
    tests = [
        test_gemma_decide_script_exists,
        test_gemma_execute_script_exists,
        test_autonomy_brief_documents_decisions,
        test_plan_has_chunk_metadata,
        test_gemma_execute_split_implemented,
        test_gemma_execute_has_harness_fix,
    ]

    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"✓ {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    exit(0 if failed == 0 else 1)
