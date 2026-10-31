#!/usr/bin/env python3
"""check_workspace's org-chart guard (2026-10-19): a script that assigns a task to a role by writing
its key is a FAIL; routing through role_store.escalate() and anything under scripts/tests/ is not.

Run: python3 scripts/tests/test_check_assignee_literals.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_workspace  # noqa: E402


def scan(files):
    with tempfile.TemporaryDirectory() as d:
        repo = Path(d)
        m = repo / "projects" / "local-inference" / "models"
        m.mkdir(parents=True)
        (m / "ROLES.json").write_text(json.dumps({"roles": {"jas": {}, "claude": {}, "lead": {}}}))
        (repo / "scripts" / "tests").mkdir(parents=True)
        for rel, text in files.items():
            (repo / rel).write_text(text)
        return check_workspace.hardcoded_assignees(repo)


def test_a_literal_role_key_is_found():
    hits = scan({"scripts/a.py": 'ts.set_meta(t, needs="claude")\n'})
    assert hits and hits[0].startswith("scripts/a.py:1"), hits


def test_a_new_role_from_the_chart_is_covered_too():
    assert scan({"scripts/a.py": "add(t, assigned='lead')\n"})


def test_escalate_is_clean():
    assert scan({"scripts/a.py": 'ts.set_meta(t, needs=role_store.escalate("technical"))\n'}) == []


def test_tests_and_comments_are_exempt():
    assert scan({"scripts/tests/test_x.py": 'needs="jas"\n', "scripts/b.py": '# needs="jas" was the old way\n'}) == []


def test_production_is_not_a_role():
    assert scan({"scripts/a.py": 'ts.set_meta(t, needs="")\n'}) == []


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS {n}")
        except Exception as e:                                      # noqa: BLE001
            bad += 1
            print(f"FAIL {n}: {type(e).__name__}: {e}")
    print(f"{len(tests) - bad} passed, {bad} failed")
    sys.exit(1 if bad else 0)
