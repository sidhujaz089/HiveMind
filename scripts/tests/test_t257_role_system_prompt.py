#!/usr/bin/env python3
"""Oracle for t257: the fast admin role's system prompt is generated from its tool list.

Pure function test: role_system_prompt takes a {tool_name: bool} dict and returns a
system-prompt block. No model call, no live state, no server -- a fake dict in, a
string out. Red on main because role_system_prompt does not exist yet (ImportError).
"""
import sys
from pathlib import Path

# From scripts/tests/, the repo root is parents[2]; role_caps lives in
# projects/local-inference/.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "projects" / "local-inference"))
from role_caps import role_system_prompt  # (new)

ENABLED = {
    "read_file": True,
    "search_repo": True,
    "escalate": True,
    "run_command": False,
    "web_search": False,
}


def test_enabled_tools_are_claimed():
    p = role_system_prompt(ENABLED)
    for name in ("read_file", "search_repo", "escalate"):
        lines = [ln for ln in p.splitlines() if name in ln]
        assert lines, f"enabled tool {name!r} is not named in the prompt"
        assert not all(any(w in ln.lower() for w in ("cannot", "unavailable", "no access")) for ln in lines), \
            f"enabled tool {name!r} must not be presented only as unavailable"


def test_disabled_tools_are_named_unavailable():
    p = role_system_prompt(ENABLED)
    for name in ("run_command", "web_search"):
        lines = [ln for ln in p.splitlines() if name in ln]
        assert lines, f"disabled tool {name!r} is not named at all"
        assert any(any(w in ln.lower() for w in ("cannot", "unavailable", "no access")) for ln in lines), \
            f"disabled tool {name!r} must be presented as unavailable, not as a capability"


def test_grounding_contract_present():
    p = role_system_prompt(ENABLED)
    low = p.lower()
    assert "specul" in low, "the prompt must forbid speculation"
    assert "find out" in low or "senior" in low or "ask" in low, \
        "must direct the model to lean on the senior model and say so"
    assert "relay" in low or "report back" in low, \
        "must commit to relaying the answer back to the user"


TESTS = [
    test_enabled_tools_are_claimed,
    test_disabled_tools_are_named_unavailable,
    test_grounding_contract_present,
]

if __name__ == "__main__":
    failures = 0
    for fn in TESTS:
        try:
            fn()
            print("ok", fn.__name__)
        except AssertionError as e:
            failures += 1
            print("FAIL", fn.__name__, "-", e)
    sys.exit(1 if failures else 0)
