#!/usr/bin/env python3
"""t1560: console.py's run_command tool must REFUSE a git command that changes the LIVE
checkout's HEAD, index or working tree, and must RUN the same verb in a scratch tree outside it.

Red on main for the RCA's reason, not its own: the incident command line -- cd <repo> && git
checkout -b manager/t999-land-target-context && git apply /tmp/t999b/t999-coder.patch -- and 17
more mutating verbs are EXECUTED. console.subprocess.run is replaced by a fake that records every
command string it is handed and raises the instant a must-refuse command reaches it, so nothing
is ever executed for real and no git state changes. console._action is replaced too, so the audit
file is never opened. No literal temp-root path appears in this file: T is assembled, as in
test_guard_tmp_copies.py.

Run: python3 scripts/tests/test_console_run_command_guard_git.py   (pytest is not installed)
"""
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import console  # noqa: E402

T = "/" + "tmp/"                     # assembled so this file carries no literal temp-root path
REPO = str(console.REPO)
SCRATCH = tempfile.mkdtemp(prefix="t1560-scratch-")

EXEC = []          # every command string that reached subprocess.run -- empty for every refusal
AUDIT = []         # every audit row console._action was asked to write -- empty for every refusal
FORBIDDEN = []     # the commands this suite declares must-refuse
PASS = 0
FAIL = 0

# (command, the verb the refusal message must name)
REFUSALS = [
    ("cd " + REPO + " && git checkout -b manager/t999-land-target-context && git apply "
     + T + "t999b/t999-coder.patch", "git checkout"),
    ("cd " + REPO + " && git checkout -b w", "git checkout"),
    ("cd " + REPO + " && git checkout .", "git checkout"),
    ("cd " + REPO + " && git checkout -b w && cp -a x y", "git checkout"),
    ("git checkout -b w", "git checkout"),
    ("cd " + REPO + " && git switch main", "git switch"),
    ("cd " + REPO + " && git reset --hard HEAD~1", "git reset"),
    ("cd " + REPO + " && git -c user.name=x commit -m m", "git commit"),
    ("cd " + REPO + " && git cherry-pick abc1234", "git cherry-pick"),
    ("cd " + REPO + " && git am " + T + "x/0001.patch", "git am"),
    ("cd " + REPO + " && git merge --no-ff feature", "git merge"),
    ("cd " + REPO + " && git rebase main", "git rebase"),
    ("cd " + REPO + " && git stash push -m wip", "git stash"),
    ("cd " + REPO + " && git restore projects/local-inference/coder.py", "git restore"),
    ("cd " + REPO + " && git branch -f main HEAD~1", "git branch"),
    ("cd " + REPO + " && git tag v1560", "git tag"),
    ("cd " + REPO + " && git -C " + REPO + " apply " + T + "x/y.patch", "git apply"),
    ("cd " + REPO + " && git checkout -b w && git log --oneline", "git checkout"),
]
UNRESOLVABLE = "cd $(cat ~/.config/wt) && git checkout -b w"

# Reads: every one of these must still EXECUTE in the live checkout. Refusing any of them is the
# false positive the task forbids: read-only git is how a model inspects the checkout at all.
READS = [
    "cd " + REPO + " && git log --oneline -n 5",
    "git -C " + REPO + " status --short",
    "cd " + REPO + " && git diff HEAD~1 HEAD -- projects/local-inference/coder.py",
    "cd " + REPO + " && git show HEAD:projects/local-inference/coder.py",
    "cd " + REPO + " && git grep -n run_command -- projects/local-inference/console.py",
    "cd " + REPO + " && git branch -a",
    "cd " + REPO + " && git branch --show-current",
    "cd " + REPO + " && git tag -l",
    "cd " + REPO + " && git remote -v",
    "cd " + REPO + " && git stash list",
    "cd " + REPO + " && git worktree list",
    "cd " + REPO + " && git rev-parse HEAD",
    "cd " + REPO + " && git rev-list --count HEAD",
    "cd " + REPO + " && git show-ref --heads",
    "cd " + REPO + " && git ls-files -- projects/local-inference",
    "cd " + REPO + " && git ls-remote --heads origin",
    "cd " + REPO + " && git cat-file -p HEAD",
    "cd " + REPO + " && git blame -L 1,10 console.py",
    "cd " + REPO + " && git merge-base HEAD HEAD~1",
    "cd " + REPO + " && git reflog -n 5",
    "cd " + REPO + " && git describe --tags",
    "cd " + REPO + " && git diff --stat HEAD~1 HEAD",
    "cd " + REPO + " && git log --author=Jas --grep=guard --oneline",
    "cd " + REPO + " && git -c core.pager=cat log -1",
]
# The same verbs, aimed at a scratch tree OUTSIDE the live checkout: all must run.
SCRATCH_CASES = [
    "cd " + SCRATCH + " && git checkout -b t1560-scratch",
    "git -C " + SCRATCH + " checkout -b t1560-scratch",
    "git -C" + SCRATCH + " checkout -b t1560-scratch",
    "cd " + REPO + " && git -C " + SCRATCH + " checkout -b t1560-scratch",
    "cd " + REPO + " && git -C " + SCRATCH + " commit -m m",
    "cd " + SCRATCH + " && git apply " + T + "x/y.patch",
    "cd " + SCRATCH + " && git checkout -b w && git apply " + T + "x/y.patch",
]


class _Proc:
    returncode = 0
    stdout = "ok\n"
    stderr = ""


def _install_fake():
    """Replace the executor (and the audit writer, when console.py has one) for the duration of
    the run. The fake refuses nothing: it RECORDS what it was handed, and a command this suite
    declared must-refuse reaching it is itself the failure -- that is the assertion that makes
    the oracle red on main for the missing guard and nothing else."""
    def fake(argv, *a, **kw):
        raw = argv if isinstance(argv, str) else " ".join(str(x) for x in argv)
        EXEC.append(raw)
        if any(f in raw for f in FORBIDDEN):
            raise AssertionError("a live-checkout git mutation EXECUTED: " + raw[:200])
        return _Proc()
    orig = (console.subprocess.run, getattr(console, "_action", None))
    patched = hasattr(console, "_action")
    console.subprocess.run = fake
    if patched:
        console._action = lambda row: AUDIT.append(row)
    return orig, patched


def _ran(cmd):
    """True when some recorded execution carried cmd (robust to the argv shape: a leading
    'bash -lc' prefix does not break substring containment)."""
    return any(cmd in e for e in EXEC)


def test_mutating_verbs_are_refused_before_they_run():
    for cmd, verb in REFUSALS:
        out = console.tool_run_command({"command": cmd, "timeout_s": 60})
        assert out.startswith("REFUSED"), "a git mutation ran instead of being refused: " + cmd
        assert verb in out, "the refusal does not name " + verb + ": " + out[:200]
        assert "hivemind-tmp" in out, "the refusal names no scratch tree on disk: " + out[:200]
        assert not _ran(cmd), "the refused command reached the executor: " + cmd
    out = console.tool_run_command({"command": UNRESOLVABLE, "timeout_s": 60})
    assert out.startswith("REFUSED"), "an unresolvable target must be refused: " + out[:200]
    assert "cannot be resolved" in out, "no reason given for the unresolvable target: " + out[:200]
    assert not _ran(UNRESOLVABLE), "the unresolvable target executed: " + UNRESOLVABLE


def test_a_refusal_is_not_a_write_that_was_logged():
    # A refusal returns before subprocess.run and before the audit row: a command that was
    # refused must not appear in the record as a write that happened.
    assert AUDIT == [], "a refusal wrote an audit row: " + repr(AUDIT[:1])
    assert EXEC == [], "a refusal reached the executor: " + repr(EXEC[:1])


def test_read_only_git_still_runs_in_the_live_checkout():
    for cmd in READS:
        out = console.tool_run_command({"command": cmd, "timeout_s": 60})
        assert not out.startswith("REFUSED"), "a read-only git command was refused: " + cmd
        assert _ran(cmd), "the read never reached the executor: " + cmd


def test_the_same_verbs_run_in_a_scratch_tree_outside_the_checkout():
    for cmd in SCRATCH_CASES:
        out = console.tool_run_command({"command": cmd, "timeout_s": 60})
        assert not out.startswith("REFUSED"), "refused inside a scratch tree: " + cmd
        assert _ran(cmd), "the scratch-tree command never reached the executor: " + cmd


def main():
    global PASS, FAIL
    for cmd, _verb in REFUSALS:
        FORBIDDEN.append(cmd)
    (orig_run, orig_action), patched = _install_fake()
    try:
        for fn in (test_mutating_verbs_are_refused_before_they_run,
                   test_a_refusal_is_not_a_write_that_was_logged,
                   test_read_only_git_still_runs_in_the_live_checkout,
                   test_the_same_verbs_run_in_a_scratch_tree_outside_the_checkout):
            try:
                fn()
            except Exception as e:            # an import/keyerror reports as a named FAIL, never
                FAIL += 1                      # as a crash the builder reads as its own fault
                print("  [FAIL] " + fn.__name__ + ": " + str(e))
            else:
                PASS += 1
                print("  [ok]   " + fn.__name__)
    finally:
        console.subprocess.run = orig_run
        if patched:
            console._action = orig_action
        shutil.rmtree(SCRATCH, ignore_errors=True)
    print(("FAILED " if FAIL else "ok ") + str(PASS) + " passed, " + str(FAIL) + " failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
