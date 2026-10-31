#!/usr/bin/env python3
"""Oracle for t1344: scripts/hooks/pre-commit gates a drifted skill registry.

The task wires `skills.py registry --check` into the pre-commit hook, scoped to a session id so an
ordinary commit pays nothing. This fixture pins that behaviour, not the prose: the hook is run as
an executable in a throwaway tree against STUB helpers it resolves by its own path idiom
(`$(dirname "$0")/../<helper>`, which is how its signature_gate, library, related_tests and
check_enforcement gates resolve theirs -- core.hooksPath points at scripts/hooks, so the hook's
helpers sit one level up from the hook).

Two stubs make this hermetic. scripts/library.py exits 0 unconditionally, so the file's unguarded
`library.py verify --staged` gate cannot block for the reason the r3 draft hit (a missing helper
exits non-zero and the hook fails for the absence, not for the drift). scripts/skills.py prints the
argv it was passed and exits with a scripted code, so the test can assert WHICH subcommand and
WHICH flags the hook runs, and drive the exit code the gate must react to.

RED ON MAIN: the 215-line hook contains no reference to `registry --check` and none to
DSH_SESSION_ID, so the text tests fail; the behaviour test fails because a hook with no such gate
exits 0 on a registry the store has outgrown.

Live state: none. Everything happens under a TemporaryDirectory; no git repo, no .git/hooks, no
model call, no fixed /tmp path, every subprocess carries timeout=180.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / "scripts" / "hooks" / "pre-commit"

STUB_LIBRARY = "#!/usr/bin/env python3\nimport sys\nsys.exit(0)\n"
STUB_SKILLS = (
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "sys.stdout.write(' '.join(sys.argv[1:]) + chr(10))\n"
    "sys.stdout.write('missing: beta-skill\\n')\n"
    "sys.exit(int(open(sys.argv[0] + '.rc').read().strip()))\n"
)


def read_hook():
    return HOOK.read_text(encoding="utf-8")


def stage(root, rc):
    """A tree shaped like scripts/ + scripts/hooks/, so the hook's ../ helpers resolve."""
    (root / "scripts" / "hooks").mkdir(parents=True)
    hook = root / "scripts" / "hooks" / "pre-commit"
    hook.write_text(read_hook(), encoding="utf-8")
    hook.chmod(0o755)
    lib = root / "scripts" / "library.py"
    lib.write_text(STUB_LIBRARY, encoding="utf-8")
    lib.chmod(0o755)
    sk = root / "scripts" / "skills.py"
    sk.write_text(STUB_SKILLS, encoding="utf-8")
    sk.chmod(0o755)
    (Path(str(sk) + ".rc")).write_text("{}\n".format(rc), encoding="utf-8")
    return hook


def test_hook_runs_the_registry_check():
    text = read_hook()
    assert "skills.py" in text, \
        "pre-commit never invokes skills.py; the reconciliation has no fixture"
    assert "registry --check" in text, \
        "the hook does not run the reconciliation -- 'registry --check' is absent, so a drift "\
        "that RCA 433601fc1c12 could not see is still invisible"
    assert "DSH_SESSION_ID" in text, \
        "the gate is not scoped to a session id, so it fires on every commit and is --no-verify'd"


def test_hook_blocks_a_registry_the_store_has_outgrown():
    """Behaviour: skills.py registry --check exits 1 -> the commit is blocked, and the check's
    own lines reach the output so the failure is diagnosable (never-truncate-a-record)."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        hook = stage(root, rc=1)
        env = dict(os.environ)
        env["DSH_SESSION_ID"] = "s-hook"
        r = subprocess.run([str(hook)], capture_output=True, text=True, timeout=180,
                           cwd=str(root), env=env)
        out = r.stdout + r.stderr
        assert r.returncode == 1, \
            "the hook exited {} on a registry that has drifted from the store; it must block. "\
            "stdout: {} stderr: {}".format(r.returncode, out[-1200:], out[-900:])
        assert "registry --check" in out, \
            "the hook did not run the check as a registry --check invocation; argv seen: " + out[-1200:]
        assert "--session" in out and "s-hook" in out, \
            "the check must be asked about THIS session; argv seen: " + out[-1200:]
        assert "beta-skill" in out, \
            "the hook blocked the commit but printed none of the check's lines: " + out[-1200:]
        assert "COMMIT BLOCKED" in out, \
            "the block must use the file's own fail() so it names the fix: " + out[-1200:]
        assert "skills.py registry --session" in out, \
            "the block must name the command that records the store: " + out[-1200:]


def test_hook_stays_quiet_for_a_commit_with_no_session_id():
    """Cost budget: no session id -> the gate is not reached, and an ordinary commit passes.
    The stub skills.py exits 1 here too, so the ONLY way this returns 0 is a real guard."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        hook = stage(root, rc=1)
        env = dict(os.environ)
        env["DSH_SESSION_ID"] = ""
        r = subprocess.run([str(hook)], capture_output=True, text=True, timeout=180,
                           cwd=str(root), env=env)
        out = r.stdout + r.stderr
        assert r.returncode == 0, \
            "the hook blocked an ordinary commit with no session id (rc={}): {}".format(
                r.returncode, out[-1200:])
        assert "registry --check" not in out, \
            "the gate ran with no session id; it must be guarded: " + out[-1200:]


def test_hook_fires_when_a_session_id_is_present():
    """The same stub, the same tree, a session id set: the gate must be reached. This is the
    pair that makes the guard real in both directions -- the previous test cannot be passed by
    a gate that is simply never run."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        hook = stage(root, rc=1)
        env = dict(os.environ)
        env["DSH_SESSION_ID"] = "s-present"
        r = subprocess.run([str(hook)], capture_output=True, text=True, timeout=180,
                           cwd=str(root), env=env)
        assert r.returncode == 1, \
            "a session that recorded a registry must be gated; rc={}: {}".format(
                r.returncode, (r.stdout + r.stderr)[-1200:])


TESTS = [test_hook_runs_the_registry_check,
         test_hook_blocks_a_registry_the_store_has_outgrown,
         test_hook_stays_quiet_for_a_commit_with_no_session_id,
         test_hook_fires_when_a_session_id_is_present]

if __name__ == "__main__":
    failed = []
    for t in TESTS:
        try:
            t()
            print("  ok   {}".format(t.__name__))
        except Exception as e:            # noqa: BLE001 -- a crash IS a failure; report its cause
            failed.append(t.__name__)
            print("  FAIL {}: {}: {}".format(t.__name__, type(e).__name__, str(e)[:600]))
    print("\n{} of {} passed".format(len(TESTS) - len(failed), len(TESTS)))
    sys.exit(1 if failed else 0)
