#!/usr/bin/env python3
"""scripts/bridge_trigger.py: a manager answer's chunks block becomes a branch, never main (t323).

WHY. bridge.py (p52) writes briefs, oracles, the ceiling and work-list rows from a chunks
block, but nothing ran it: Claude still did it by hand after every manager pass. The trigger
runs it after each pass, on a branch bridge/<task> made in a throwaway worktree, so Claude's
only job is to review and merge that branch -- and the merge is the oracle freeze (D-35).

Model-free. Every case builds a throwaway git repo; the live repository is never touched.
The contract is the module docstring of scripts/bridge_trigger.py.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


LOOP = """#!/bin/bash
work_list () { cat <<'LIST'
p07-keepalive|brief-p07.txt|scripts/a.py|scripts/tests/test_a.py
LIST
}
"""
ORACLE_TEXT = "import sys\nsys.path.insert(0, 'scripts')\nimport widget  # noqa\n"
ORACLE = {"kind": "oracle", "file": "scripts/tests/test_widget.py", "oracle_text": ORACLE_TEXT,
          "red_because": "scripts/widget.py does not exist yet"}
IMPL = {"kind": "implementation", "file": "scripts/widget.py",
        "target": "scripts/tests/test_widget.py",
        "single_change": "Create scripts/widget.py with spin() returning 3.",
        "what_is_wrong": "Nothing spins the widget.",
        "done_when": "python3 scripts/tests/test_widget.py exits 0",
        "related_entries": ["none"]}


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def fixture():
    repo = Path(tempfile.mkdtemp(prefix="trigger-test-"))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "trigger test")
    (repo / "scripts" / "tests").mkdir(parents=True)
    (repo / "workspace" / "briefs").mkdir(parents=True)
    (repo / "workspace" / "tasks").mkdir(parents=True)
    (repo / "scripts" / "autonomy_loop.sh").write_text(LOOP)
    (repo / "workspace" / "test-failures-ceiling.json").write_text(json.dumps({}))
    (repo / "workspace" / "briefs" / ".keep").write_text("")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fixture")
    return repo


def answer(chunks):
    """Written OUTSIDE the repo, as the live answers are outside any bridge worktree."""
    f = Path(tempfile.mkdtemp(prefix="trigger-ans-")) / "t999.md"
    body = "## t999\n\nProse.\n\n"
    if chunks is not None:
        body += "```chunks\n" + (chunks if isinstance(chunks, str) else json.dumps(chunks)) + "\n```\n"
    f.write_text(body)
    return f


def state(repo):
    return (git(repo, "rev-parse", "main").stdout.strip(),
            git(repo, "status", "--porcelain").stdout,
            git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip())


def worktrees(repo):
    return git(repo, "worktree", "list", "--porcelain").stdout.count("worktree ")


def branch_exists(repo, b):
    return git(repo, "rev-parse", "--verify", "-q", f"refs/heads/{b}").returncode == 0


def main():
    import bridge_trigger as bt

    # 1. A prose answer is not an error and writes nothing.
    repo, notes = fixture(), []
    before = state(repo)
    rc = bt.run(answer(None), "t999", "qwen-test:1", repo=repo, note=lambda t, x: notes.append((t, x)))
    report("no block: exit 0", rc, 0)
    report("no block: no branch", branch_exists(repo, "bridge/t999"), False)
    report("no block: no note", notes, [])
    report("no block: main untouched", state(repo), before)

    # 2. A block bridge.py refuses goes back to the task as ONE note naming the error.
    repo, notes = fixture(), []
    before = state(repo)
    rc = bt.run(answer("[{not json"), "t999", "qwen-test:1", repo=repo,
                note=lambda t, x: notes.append((t, x)))
    report("refused: exit 2", rc, 2)
    report("refused: exactly one note, on the task", [t for t, _ in notes], ["t999"])
    report("refused: the note names the error", bool(notes) and "not JSON" in notes[0][1], True)
    report("refused: no branch", branch_exists(repo, "bridge/t999"), False)
    report("refused: no worktree left behind", worktrees(repo), 1)
    report("refused: main untouched", state(repo), before)

    repo, notes = fixture(), []
    rc = bt.run(answer([dict(IMPL, done_when="")]), "t999", "qwen-test:1", repo=repo,
                note=lambda t, x: notes.append((t, x)))
    report("refused (missing field): exit 2", rc, 2)
    report("refused (missing field): note names the field",
           bool(notes) and "done_when" in notes[0][1], True)

    # 3. A valid block becomes one commit on bridge/<task>; main never moves.
    repo, notes = fixture(), []
    before = state(repo)
    ans = answer([ORACLE, IMPL])
    rc = bt.run(ans, "t999", "qwen-test:1", repo=repo, note=lambda t, x: notes.append((t, x)))
    report("valid: exit 0", rc, 0)
    report("valid: branch bridge/t999 exists", branch_exists(repo, "bridge/t999"), True)
    report("valid: main untouched", state(repo), before)
    report("valid: no worktree left behind", worktrees(repo), 1)
    # The branch waits on Claude, so the task is held for Claude -- otherwise the manager's rotation
    # takes it straight back and re-scopes it (t469, 2026-10-01: 95 minutes, then a timeout).
    report("valid: one note, holding the task for Claude's review",
           len(notes) == 1 and "waits for Claude" in notes[0][1], True)
    report("valid: exactly one commit on the branch",
           git(repo, "rev-list", "--count", "main..bridge/t999").stdout.strip(), "1")
    files = set(git(repo, "diff", "--name-only", "main", "bridge/t999").stdout.split())
    report("valid: the oracle is on the branch", "scripts/tests/test_widget.py" in files, True)
    report("valid: a brief is on the branch",
           any(f.startswith("workspace/briefs/brief-") for f in files), True)
    report("valid: the work list gained its row", "scripts/autonomy_loop.sh" in files, True)
    report("valid: the ceiling declares the red oracle",
           "workspace/test-failures-ceiling.json" in files, True)
    msg = git(repo, "log", "-1", "--format=%B", "bridge/t999").stdout
    report("valid: the commit names the task", "t999" in msg.splitlines()[0], True)
    report("valid: the trailer names the manager model", "Agent: ollama/qwen-test:1" in msg, True)
    report("valid: the oracle text is exactly the block's",
           git(repo, "show", "bridge/t999:scripts/tests/test_widget.py").stdout, ORACLE_TEXT)

    # 4. A second block while the first still waits on Claude is refused, not stacked.
    tip = git(repo, "rev-parse", "bridge/t999").stdout.strip()
    notes = []
    rc = bt.run(ans, "t999", "qwen-test:1", repo=repo, note=lambda t, x: notes.append((t, x)))
    report("branch exists: exit 3", rc, 3)
    report("branch exists: branch unchanged", git(repo, "rev-parse", "bridge/t999").stdout.strip(), tip)
    report("branch exists: no note", notes, [])
    report("branch exists: main untouched", state(repo), before)

    # 5. Once merged, rerunning the same block has nothing new to apply: no branch.
    git(repo, "branch", "-D", "bridge/t999")
    git(repo, "merge", "-q", "--ff-only", tip)
    merged = state(repo)
    rc = bt.run(ans, "t999", "qwen-test:1", repo=repo, note=lambda t, x: notes.append((t, x)))
    report("already applied: exit 0", rc, 0)
    report("already applied: no new branch", branch_exists(repo, "bridge/t999"), False)
    report("already applied: main untouched", state(repo), merged)

    # 6. New test files are registered on the branch: the live pre-commit hook refuses an
    #    unregistered script, so without this every real bridge commit would be blocked.
    repo = fixture()
    (repo / "scripts" / "library.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "if sys.argv[1] == 'register':\n"
        "    log = Path(__file__).resolve().parent / 'registered.txt'\n"
        "    log.write_text((log.read_text() if log.exists() else '') + sys.argv[2] + '\\n')\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fake library")
    rc = bt.run(answer([ORACLE, IMPL]), "t999", "qwen-test:1", repo=repo, note=lambda t, x: None)
    reg = git(repo, "show", "bridge/t999:scripts/registered.txt").stdout
    report("library present: exit 0", rc, 0)
    report("library present: the new oracle was registered on the branch",
           "scripts/tests/test_widget.py" in reg, True)

    # 7. The CLI reaches the same code.
    repo = fixture()
    rc = bt.main([str(answer(None)), "--task", "t999", "--model", "qwen-test:1", "--repo", str(repo)])
    report("CLI: a prose answer exits 0", rc, 0)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
