"""t710: the actions Claude Code's auto-mode classifier refused on 2026-10-06 are
granted as explicit allow rules in the TRACKED .claude/settings.json.

WHY THIS FILE AND NOT .claude/settings.local.json: settings.local.json is gitignored
(git ls-files .claude lists settings.json and settings.local.json.bak, never
settings.local.json). A grant written there is invisible on a fresh clone, in every
code review, and in git log -- which is the shape RCA 31e21653e2e6 recorded for the
2026-09-28 hand-edit: a capability "recorded only in prose", discovered by a human days
later. The tracked file's own header says it: "this file holds rules that must survive
a fresh clone."

The rules granted here are exactly the two Jas approved in words on 2026-10-06:
    ollama rm <tag>                  (deleting an OLLAMA model)
    python3 scripts/models.py drop <tag>
NOT granted, deliberately: Bash(kill:*). The refusal record's own note calls a blanket
kill:* "wide"; the contending job was fixed at source (1c983cee -- the post-commit hook
now honours the scheduler pause), and an allow rule cannot be narrowed to a pid, so
there is no narrow form of it to grant.

Run:  scripts/tests/test_settings_permission_grants.py
No pytest is used. Exits non-zero if any test fails. Touches nothing: reads one file,
runs one read-only `git ls-files`.
"""

import json
import subprocess
import sys
from pathlib import Path

# From scripts/tests/, the repository root is two levels up (parents[2]).
REPO_ROOT = Path(__file__).resolve().parents[2]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"

# Real constants, copied from the task's refusal record (t710 note, 2026-10-06): the
# command, then ":*" so any arguments match. Exact strings -- the harness matches the
# whole pattern string, so a near-miss that reads as granted grants nothing.
GRANTED = [
    "Bash(ollama rm:*)",
    "Bash(python3 scripts/models.py drop:*)",
]

# Shapes this scope must NOT grant. Distinct real values: a build that over-grants a
# destructive command passes every positive assertion above and fails this one.
NOT_GRANTED = [
    "Bash(kill:*)",
    "Bash(killall:*)",
    "Bash(rm:*)",
    "Bash(rm -rf:*)",
    "Bash(sudo:*)",
    "Bash(git push --force:*)",
]

# Standing content of the file, verbatim, that must survive the edit.
PRESERVED_DENY = "mcp__ff5d2144-4ece-468d-809e-ce1da6c27f1c__share_file"
PRESERVED_CONTEXT_HOOK = 'python3 "$CLAUDE_PROJECT_DIR/scripts/context_watch.py" --hook'
PRESERVED_TASK_HOOK = 'python3 "$CLAUDE_PROJECT_DIR/scripts/hooks/guard_task_writes.py"'


def _data():
    return json.loads(SETTINGS.read_text(encoding="utf-8"))


def _allow():
    data = _data()
    perms = data.get("permissions", {})
    allow = perms.get("allow")
    assert isinstance(allow, list), (
        "permissions.allow is %r in the tracked .claude/settings.json. The grant must be "
        "a list of rule strings, not %s: an allow entry is a tool-permission pattern and "
        "the harness matches the exact string, so a misspelled key is a silent no-op -- a "
        "permission believed granted is worse than none." % (allow, type(allow).__name__))
    return allow


def test_settings_json_is_valid_json():
    json.loads(SETTINGS.read_text(encoding="utf-8"))


def test_granted_rules_are_in_the_allow_list():
    # Membership in permissions.allow -- NOT "the string appears somewhere in the file",
    # which a grant written into deny would satisfy.
    allow = _allow()
    for rule in GRANTED:
        assert rule in allow, (
            "%r is not in permissions.allow of .claude/settings.json; without it the "
            "2026-10-06 refusal (ollama model deletion) re-lands on the next session and "
            "the action is handed to Jas again." % rule)


def test_grants_are_exact_patterns_not_loose_prefixes():
    # "Bash(ollama rm:*)" is the shape the harness matches. "Bash(ollama rm*)" and
    # "Bash(ollama rm *)" look granted and match nothing. One exact entry per command.
    allow = _allow()
    for rule in GRANTED:
        head = rule[: -len(":*)")]
        matches = [a for a in allow if isinstance(a, str)
                   and a.startswith(head) and a.endswith(")")]
        assert matches == [rule], (
            "for %r expected exactly one exact-form entry, found %r -- a prefix, a "
            "wildcard in the wrong place, or a duplicate is a grant that reads as armed "
            "and does not fire." % (rule, matches))


def test_wide_destructive_grants_are_absent():
    allow = _allow()
    for rule in NOT_GRANTED:
        assert rule not in allow, (
            "%r was granted. t710 asks for two named commands, not a blanket grant of a "
            "destructive one -- the refusal record's own note calls a blanket kill:* wide."
            % rule)


def test_no_grant_is_placed_in_the_deny_list():
    # The exact inverse of what is asked: a rule in deny satisfies "the string is in the
    # file" while refusing the action.
    deny = _data().get("permissions", {}).get("deny", [])
    for rule in GRANTED:
        assert rule not in deny, (
            "%r appears in permissions.deny -- that refuses the action, it does not grant "
            "it." % rule)


def test_existing_deny_and_hooks_survive_the_edit():
    data = _data()
    deny = data["permissions"]["deny"]
    assert PRESERVED_DENY in deny, "the share_file deny was lost: %r" % (deny,)
    ctx = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    assert ctx == PRESERVED_CONTEXT_HOOK, (
        "the context guard hook command changed: %r" % (ctx,))
    pre = [h["command"] for entry in data["hooks"]["PreToolUse"] for h in entry["hooks"]]
    assert PRESERVED_TASK_HOOK in pre, (
        "the task-write guard was dropped from PreToolUse: %r" % (pre,))


def test_the_grant_lives_in_a_tracked_file():
    # The whole reason the grant goes in settings.json: an untracked permission is not in
    # the repo, so it cannot be reviewed, and the next clone is back to asking Jas.
    r = subprocess.run(["git", "ls-files", "--", ".claude/settings.json"],
                       cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "git ls-files failed: %s" % r.stderr.strip()
    assert r.stdout.strip() == ".claude/settings.json", (
        ".claude/settings.json is not tracked in git (ls-files: %r) -- a permission grant "
        "that is not tracked is invisible to review and dies with the machine."
        % (r.stdout.strip(),))


TESTS = [
    test_settings_json_is_valid_json,
    test_granted_rules_are_in_the_allow_list,
    test_grants_are_exact_patterns_not_loose_prefixes,
    test_wide_destructive_grants_are_absent,
    test_no_grant_is_placed_in_the_deny_list,
    test_existing_deny_and_hooks_survive_the_edit,
    test_the_grant_lives_in_a_tracked_file,
]


if __name__ == "__main__":
    # Tracked, so present in a clone; the guard is for a checkout that lacks it (the
    # sibling pattern recorded at scripts/tests/test_settings_local_permissions.py:72-78).
    if not SETTINGS.is_file():
        print(f"SKIP: {SETTINGS} does not exist here -- nothing to check")
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
