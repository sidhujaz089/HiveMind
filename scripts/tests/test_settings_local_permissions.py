"""t687: the temporary s04 permission rules (console.py / chatui.py / role_caps)
are removed from .claude/settings.local.json, and the four standing infra rules
still are.

Run:  python3 scripts/tests/test_settings_local_permissions.py
No pytest is used. Exits non-zero if any test fails.
"""

import json
import sys
from pathlib import Path

# From scripts/tests/, the repository root is two levels up (parents[2]).
REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = REPO_ROOT / ".claude" / "settings.local.json"

# The four rules t687 removes -- scoped to the three s04 artifacts.
REMOVED = [
    "Edit(~/ai-workspace/projects/local-inference/console.py)",
    "Edit(~/ai-workspace/projects/local-inference/chatui.py)",
    "Edit(~/ai-workspace/projects/local-inference/models/ROLE-CAPS.json)",
    "Bash(python3 projects/local-inference/role_caps.py:*)",
]

# The four standing rules that must remain -- distinct real constants, not a count.
KEPT = [
    "Bash(systemctl --user restart model-chat)",
    "Bash(systemctl --user restart agent-console)",
    "Bash(git merge --no-ff bridge/*)",
    "Bash(git merge --no-ff coder/*)",
]


def _allow():
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return data["permissions"]["allow"]


def test_file_is_valid_json_with_allow_list():
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    assert "permissions" in data, "settings.local.json lost its permissions key"
    assert isinstance(data["permissions"]["allow"], list), "permissions.allow is not a list"


def test_temporary_rules_are_removed():
    allow = _allow()
    for rule in REMOVED:
        assert rule not in allow, "temporary rule still present: %s" % rule


def test_standing_infra_rules_remain():
    allow = _allow()
    for rule in KEPT:
        assert rule in allow, "standing rule was lost: %s" % rule


def test_allow_is_exactly_the_standing_set():
    # Pins the value, not the shape: allow is precisely the four infra rules.
    allow = _allow()
    assert set(allow) == set(KEPT), "allow=%r, expected exactly %r" % (allow, KEPT)
    assert len(allow) == 4, "allow has %d entries, expected 4" % len(allow)


TESTS = [
    test_file_is_valid_json_with_allow_list,
    test_temporary_rules_are_removed,
    test_standing_infra_rules_remain,
    test_allow_is_exactly_the_standing_set,
]


if __name__ == "__main__":
    # GITIGNORED, SO ABSENT IN A CLONE (2026-10-06): the builder's sandbox is a clone, this file is not in it,
    # and a failure here made coder.py refuse EVERY build ("undeclared failure"). Absent = not this machine's
    # settings to check: say so and pass. Present = every check below applies.
    if not SETTINGS.is_file():
        print(f"SKIP: {SETTINGS} does not exist here (gitignored; a clone or sandbox) -- nothing to check")
        raise SystemExit(0)
    failures = 0
    for fn in TESTS:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print("FAIL %s: %s" % (fn.__name__, exc))
        except Exception as exc:
            failures += 1
            print("ERROR %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
        else:
            print("ok   %s" % fn.__name__)
    if failures:
        print("%d test(s) failed" % failures)
        sys.exit(1)
    print("all tests passed")
