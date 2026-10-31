#!/usr/bin/env python3
"""t476: a merge into main is refused unless the committer's ROLE has 'merge_review'.

Jas, 2026-09-29, on the DeepSeek Harness: "I just really want to make sure that its permissions
are set properly so I can't do something it shouldn't be allowed to." Until this, the Harness's
empty task list in ROLES.json was checked by nothing: it could `git merge coder/...` into main and
every hook waved the commit through, because commit-msg exits 0 on any subject starting "Merge ".

A FIXTURE REPOSITORY, NEVER THIS ONE. It gets copies of the real commit-msg and
reference-transaction hooks, the real role_gate.py and a ROLES.json, so what is tested is the
shipped hooks acting on real git operations -- not a function called with made-up arguments.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
ROLES_REL = Path("projects/local-inference/models/ROLES.json")
PASSED = FAILED = 0


def report(name, ok, detail=""):
    global PASSED, FAILED
    PASSED += bool(ok)
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"\n        {detail}"))


def git(repo, *args, env=None):
    e = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
             GIT_COMMITTER_EMAIL="t@t", **(env or {}))
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, env=e,
                          timeout=60)


def msg(subject, agent):
    return f"{subject}\n\nAgent: {agent}\n" if agent else f"{subject}\n"


def fixture(tmp, dsh_tasks=()):
    r = Path(tmp) / "repo"
    (r / "scripts" / "hooks").mkdir(parents=True)
    for h in ("commit-msg", "reference-transaction"):
        shutil.copy2(REPO / "scripts" / "hooks" / h, r / "scripts" / "hooks" / h)
    shutil.copy2(REPO / "scripts" / "role_gate.py", r / "scripts" / "role_gate.py")
    roles = json.loads((REPO / ROLES_REL).read_text(encoding="utf-8"))
    roles["roles"]["dsh"]["tasks"] = [{"task": t, "interrupt": False, "teamwork": False}
                                      for t in dsh_tasks]
    # THE TASK LISTS UNDER TEST ARE PINNED; only the model tags come from the live store, so an agent
    # still maps to its role. On 2026-10-02 Jas took Scoping off the Manager on the Models tab and the
    # freeze case went red on main -- and a red suite holds every coder build (t650). A Models-tab
    # edit must not be able to do that.
    T = lambda *ks: [{"task": k, "interrupt": False, "teamwork": False} for k in ks]
    roles["roles"]["manager"]["tasks"] = T("supervision", "scoping", "console")
    # THE MANAGER'S MODEL IS PINNED TOO (2026-10-06): the cases below sign as
    # ollama/qwen3.8:27b-mtp-q4_K_M, and when Jas's Models tab moved every role to Swift that agent
    # stopped mapping to the Manager -- the freeze case went red and coder.py refused to build.
    roles["roles"]["manager"]["model"] = "qwen3.8:27b-mtp-q4_K_M"
    roles["roles"]["claude"]["tasks"] = T("merge_review", "direct_commit", "done_check", "assigned_claude")
    (r / ROLES_REL).parent.mkdir(parents=True)
    (r / ROLES_REL).write_text(json.dumps(roles), encoding="utf-8")
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "core.hooksPath", "scripts/hooks")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", msg("chore(workspace): base", "claude-code/opus-5.5"))
    git(r, "checkout", "-q", "-b", "coder/brief-aaaa")
    (r / "a.txt").write_text("built\n")
    git(r, "add", "a.txt")
    git(r, "commit", "-q", "-m", msg("feat(workspace): build", "ollama/gemma4:26b"))
    git(r, "checkout", "-q", "main")
    (r / "b.txt").write_text("main moved\n")   # so a merge is a real merge, not a fast-forward
    git(r, "add", "b.txt")
    git(r, "commit", "-q", "-m", msg("chore(workspace): move main", "claude-code/opus-5.5"))
    return r


def merge(r, agent, *extra):
    return git(r, "merge", "--no-ff", *extra, "-m",
               msg("feat(workspace): merge p1 -- coder/brief-aaaa", agent), "coder/brief-aaaa")


def head_is_merge(r):
    return len(git(r, "rev-list", "--parents", "-n1", "HEAD").stdout.split()) == 3


def main():
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        p = merge(r, "dsh-harness/qwen3.8-27b-mtp-q4_K_M")
        report("the DeepSeek Harness cannot merge a coder branch into main",
               p.returncode != 0 and not head_is_merge(r), p.stdout + p.stderr)
        report("  and the refusal names the missing task and where to enable it",
               "merge_review" in (p.stdout + p.stderr) and "Models" in (p.stdout + p.stderr),
               p.stdout + p.stderr)
        git(r, "merge", "--abort")

    # JAS MAY NOT MERGE (Jas, 2026-09-30: "No, I should not have the ability to merge. Only you do.
    # Deep Seek Harness should be able to if I turn that ability on in its role settings").
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        p = merge(r, "human/jas")
        report("Jas (no merge_review on his list, by his decision) cannot merge",
               p.returncode != 0 and not head_is_merge(r), p.stdout + p.stderr)
        git(r, "merge", "--abort")

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        p = merge(r, "claude-code/opus-5.5")
        report("Claude (merge_review on its list) can merge", p.returncode == 0 and head_is_merge(r),
               p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        p = git(r, "merge", "--no-ff", "--no-edit", "coder/brief-aaaa")
        report("a merge with git's default message (no Agent trailer) is refused",
               p.returncode != 0 and not head_is_merge(r), p.stdout + p.stderr)
        git(r, "merge", "--abort")

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        p = merge(r, "codex/gpt-5")
        report("an agent no role claims is refused (fail closed)",
               p.returncode != 0 and not head_is_merge(r), p.stdout + p.stderr)
        git(r, "merge", "--abort")

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, dsh_tasks=("merge_review",))
        p = merge(r, "dsh-harness/qwen3.8-27b-mtp-q4_K_M")
        report("the gate READS the role's list: give the Harness merge_review and it may merge",
               p.returncode == 0 and head_is_merge(r), p.stdout + p.stderr)

    for agent, allowed, name in (
            ("ollama/qwen3.8:27b-mtp-q4_K_M", True,
             "the Manager's oracle freeze (bridge/*, needs scoping) still merges on its own"),
            ("dsh-harness/qwen3.8-27b-mtp-q4_K_M", False,
             "  but the Harness cannot do the freeze either"),
            ("claude-code/opus-5.5", True,
             "  and Claude (merge_review) may merge a scope held back for its review")):
        with tempfile.TemporaryDirectory() as tmp:
            r = fixture(tmp)
            git(r, "checkout", "-q", "-b", "bridge/t9", "main~1")
            (r / "scripts" / "tests").mkdir(parents=True)
            (r / "scripts" / "tests" / "test_t9.py").write_text("# frozen oracle\n")
            git(r, "add", "-A")
            git(r, "commit", "-q", "-m", msg("test(workspace): oracle for t9", agent))
            git(r, "checkout", "-q", "main")
            p = git(r, "merge", "--no-ff", "-m",
                    msg("chore(workspace): freeze bridge/t9 -- checks passed", agent), "bridge/t9")
            report(name, (p.returncode == 0 and head_is_merge(r)) == allowed, p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        git(r, "checkout", "-q", "coder/brief-aaaa")
        p = git(r, "merge", "--no-ff", "--no-edit", "main")
        report("merging main INTO a coder branch (merge_gate.py's direction) is not gated",
               p.returncode == 0 and head_is_merge(r), p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        git(r, "merge", "--squash", "coder/brief-aaaa")
        p = git(r, "commit", "-m", msg("feat(workspace): squash p1", "dsh-harness/qwen3.8-27b"))
        report("a squash merge by the Harness is refused too",
               p.returncode != 0, p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        (r / "c.txt").write_text("x\n")
        git(r, "add", "c.txt")
        p = git(r, "commit", "-m", msg("docs(workspace): a note", "dsh-harness/qwen3.8-27b"))
        # REVERSED 2026-09-30 by Jas's decision: this used to assert the Harness MAY commit on main
        # directly ("not a merge, not refused"). A direct commit now needs direct_commit.
        report("an ordinary commit by the Harness on main is refused (direct_commit, 2026-09-30)",
               p.returncode != 0, p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        before = git(r, "rev-parse", "main").stdout.strip()
        git(r, "checkout", "-q", "-b", "coder/brief-bbbb")
        (r / "d.txt").write_text("y\n")
        git(r, "add", "d.txt")
        git(r, "commit", "-q", "-m", msg("feat(workspace): build b", "ollama/gemma4:26b"))
        git(r, "checkout", "-q", "main")
        p = git(r, "merge", "--ff-only", "coder/brief-bbbb")
        after = git(r, "rev-parse", "main").stdout.strip()
        report("a FAST-FORWARD of main onto a coder branch is refused (no commit, so no commit-msg)",
               p.returncode != 0 and after == before, p.stdout + p.stderr)
        p = git(r, "branch", "-f", "side", "coder/brief-bbbb")
        report("  while moving any other branch is untouched", p.returncode == 0, p.stderr)

    # DIRECT COMMITS TO MAIN (Jas, 2026-09-30): an ordinary commit on main needs direct_commit on the
    # committer's list. Claude on; the Harness only when Jas enables it; Jas and model roles off.
    def direct(r, agent, branch="main"):
        git(r, "checkout", "-q", branch)
        n = len(list(r.glob("d*.txt")))
        (r / f"d{n}.txt").write_text("edit\n")
        git(r, "add", "-A")
        before = git(r, "rev-parse", "HEAD").stdout
        p = git(r, "commit", "-q", "-m", msg("fix(workspace): a direct edit", agent))
        return p, git(r, "rev-parse", "HEAD").stdout != before

    for agent, allowed, name in (("claude-code/opus-5.5", True, "Claude"),
                                 ("claude-chat/mcp", True, "Claude - Chat (the MCP server's writes)"),
                                 ("dsh-harness/qwen3.8-27b-mtp-q4_K_M", False, "the DeepSeek Harness"),
                                 ("human/jas", False, "Jas"),
                                 ("ollama/gemma4:26b", False, "a model role")):
        with tempfile.TemporaryDirectory() as tmp:
            r = fixture(tmp)
            p, moved = direct(r, agent)
            report(f"direct commit to main by {name}: {'allowed' if allowed else 'refused'}",
                   (p.returncode == 0, moved), (allowed, allowed))
            if not allowed:
                # THE HINT MUST MATCH THE REASON (role_gate, 2026-10-25), and this assertion was written
                # before that fix. It demanded the permission hint for EVERY refusal, including an agent no
                # role claims -- whose real fault is that the trailer names nobody. That noise is exactly
                # what sent a session to the Models tab to grant a permission it already had. So the
                # assertion now asks for the hint that belongs to the case: an identified role is told
                # which task to add and where; an unidentified committer is told the trailer is the problem.
                if agent.startswith("ollama/") and "No role in ROLES.json claims" in p.stderr:
                    report("  and an unclaimed committer is told the TRAILER is the fault, not a permission",
                           "Agent: <tool>/<model>" in p.stderr and "direct_commit" not in p.stderr,
                           p.stderr[-300:])
                else:
                    report("  and the refusal names direct_commit and where to enable it",
                           "direct_commit" in p.stderr and "Models" in p.stderr, p.stderr[-300:])
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, dsh_tasks=("direct_commit",))
        p, moved = direct(r, "dsh-harness/qwen3.8-27b-mtp-q4_K_M")
        report("Jas switches direct_commit on for the Harness: it may commit to main", moved, True)
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        git(r, "checkout", "-q", "-b", "work")
        p, moved = direct(r, "dsh-harness/qwen3.8-27b-mtp-q4_K_M", branch="work")
        # 2026-10-09: an AGENT on a branch in the live checkout captures the services' commits.
        report("the Harness on a branch in the LIVE checkout is refused", not moved, p.stderr)
        report("  and the refusal says to use a worktree", "worktree" in p.stderr, p.stderr)
        p, moved = direct(r, "claude-code/opus-5.5", branch="work")
        report("Claude on a branch in the live checkout is not gated", moved, p.stderr)
        git(r, "checkout", "-q", "main")
        wt = Path(tmp) / "wt"
        git(r, "worktree", "add", "-q", str(wt), "-b", "dsh/topic", "main")
        (wt / "w.txt").write_text("edit\n")
        git(wt, "add", "-A")
        before = git(wt, "rev-parse", "HEAD").stdout
        git(wt, "commit", "-q", "-m", msg("fix(workspace): built in a worktree", "dsh-harness/swift"))
        report("the Harness on a branch in a WORKTREE is not gated",
               git(wt, "rev-parse", "HEAD").stdout != before)
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        (r / ROLES_REL).write_text("{ not json", encoding="utf-8")
        ok, _ = direct(r, "claude-code/opus-5.5")[1], None
        p, moved = direct(r, "dsh-harness/x")
        report("an unreadable role store still lets Claude commit the repair, and no one else",
               (ok, moved), (True, False))
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
