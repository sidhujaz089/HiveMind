#!/usr/bin/env python3
"""Auto-merge may take a FRONTIER branch (dsh/*, claude/*) only when every frontier condition holds.

Jas, 2026-10-28, approving Claude's proposal: "6 is better than nothing and when we get more data we can
see how to expand that." Frontier fixes had no path here at all -- the coder judge needs a BRIEF, a
SYMBOLS list and a pre-written oracle, none of which a hand-written branch has -- so every one of them
waited for a Claude lane, which is an escalation by our own definition.

Each condition gets its own case, because a gate whose refusals cannot be shown one at a time is a gate
nobody can review. The expensive steps (running suites, merge-tree, the workspace check) are injected:
this suite tests the DECISION, not the cost.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
ENV = {"PATH": "/usr/bin:/bin", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def g(repo, *args):
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, env=ENV)


def repo_with(branch_changes, base_ceiling=("a.py",), branch_ceiling=None):
    """A repo whose main has a ceiling, plus a dsh/topic branch with `branch_changes` applied."""
    d = Path(tempfile.mkdtemp()) / "repo"
    d.mkdir()
    g(d, "init", "-q", "-b", "main")
    (d / "workspace").mkdir()
    (d / "scripts" / "tests").mkdir(parents=True)
    (d / "workspace" / "test-failures-ceiling.json").write_text(
        json.dumps({"allowed_to_fail": list(base_ceiling)}))
    (d / "scripts" / "tests" / "test_old.py").write_text("def test_x():\n    pass\n")
    (d / "docs.md").write_text("docs\n")
    g(d, "add", "-A")
    g(d, "commit", "-qm", "base")
    g(d, "checkout", "-qb", "dsh/topic")
    for path, text in branch_changes.items():
        p = d / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    if branch_ceiling is not None:
        (d / "workspace" / "test-failures-ceiling.json").write_text(
            json.dumps({"allowed_to_fail": list(branch_ceiling)}))
    g(d, "add", "-A")
    g(d, "commit", "-qm", "a frontier change")
    g(d, "checkout", "-q", "main")
    return d


def main():
    import auto_merge as A
    fails = []

    def check(label, ok, detail=""):
        print(f"  [{'ok' if ok else 'FAIL'}]  {label}" + ("" if ok else f"\n        {detail}"))
        if not ok:
            fails.append(label)

    ok_mt = lambda b: (0, "tree", "")
    ok_chk = lambda b: (True, "68 passed, 0 failed")
    real_repo, real_ledger = A.REPO, A.FRONTIER_LEDGER

    def judge(changes, base_ceiling=("a.py",), branch_ceiling=None, suite=None, mt=None, chk=None):
        d = repo_with(changes, base_ceiling, branch_ceiling)
        A.REPO = d
        A.FRONTIER_LEDGER = d / "frontier.jsonl"
        try:
            return A.judge_frontier("dsh/topic", suite_at=suite or (lambda rev, t: 1),
                                    merge_tree=mt or ok_mt, check_after=chk or ok_chk)
        finally:
            A.REPO, A.FRONTIER_LEDGER = real_repo, real_ledger

    check("a branch that changes nothing is refused",
          judge({})[0] is False, str(judge({})[1]))

    ok, why, facts = judge({"docs/the-guide.md": "a page\n"})
    check("a DOCS-ONLY branch merges when the merge, ceiling and check are clean", ok, why)
    check("  and it needs no test flip", facts.get("docs_only") is True, str(facts))

    ok, why, _ = judge({"scripts/x.py": "def f():\n    return 1\n"})
    check("a CODE branch with no test is refused -- nothing proves it", not ok and "F2" in why, why)

    ok, why, _ = judge({"scripts/hooks/guard.py": "# touched\n"})
    check("a branch touching a GUARDED file is refused -- the merge rules go to a family",
          not ok and "F1" in why, why)

    ok, why, _ = judge({"scripts/tests/test_old.py": "def test_x():\n    pass  # edited\n"})
    check("editing a test that already existed is refused -- it could hide the failure it causes",
          not ok and "F3" in why, why)

    # THE CEILING CASE MUST REACH F4: a branch that only changes the ceiling is refused earlier by F2
    # ("nothing proves it"), which would have made this case pass for the wrong reason.
    def red_green_c(rev, t):
        return 0 if rev == "dsh/topic" else 1
    ok, why, _ = judge({"scripts/tests/test_new.py": "def test_y():\n    assert 1\n",
                        "workspace/test-failures-ceiling.json": json.dumps(
                            {"allowed_to_fail": ["a.py", "b.py"]})},
                       branch_ceiling=("a.py", "b.py"), suite=red_green_c)
    check("raising the ceiling is refused (F4, reached past F2/F3)", not ok and "F4" in why, why)

    ok, why, _ = judge({"scripts/tests/test_new.py": "def test_y():\n    assert 1\n"},
                       suite=lambda rev, t: 0)
    check("a test that already PASSES on main proves nothing", not ok and "F2" in why, why)

    ok, why, _ = judge({"scripts/tests/test_new.py": "def test_y():\n    assert 1\n"},
                       suite=lambda rev, t: 1)
    check("a test that fails on the branch is refused", not ok and "F2" in why, why)

    def flips(rev, t):
        return 1 if rev == "dsh/topic" else 99          # 99 = failed on main, 1 = failed on branch
    ok, why, _ = judge({"scripts/tests/test_new.py": "def test_y():\n    assert 1\n"}, suite=flips)
    check("  (that case is the same one: the branch's red is not a pass)", not ok, why)

    def red_green(rev, t):
        return 0 if rev == "dsh/topic" else 1            # red on main, green on the branch
    ok, why, facts = judge({"scripts/tests/test_new.py": "def test_y():\n    assert 1\n"}, suite=red_green)
    check("a test red on main and green on the branch merges", ok, why)
    check("  and the facts name the test", facts.get("tests") == ["scripts/tests/test_new.py"], str(facts))

    ok, why, _ = judge({"docs/a.md": "x\n"}, mt=lambda b: (1, "", "CONFLICT (content): docs/a.md"))
    check("a conflicted merge is refused before it is attempted", not ok and "F5" in why, why)

    ok, why, _ = judge({"docs/a.md": "x\n"}, chk=lambda b: (False, "the merged tree has 2 failed check(s)"))
    check("a merged tree that makes the workspace check worse is refused", not ok and "F6" in why, why)

    # THE COUNTER: a refusal records that it would have gone to Claude, so the gain is measurable.
    d = repo_with({"scripts/hooks/guard.py": "# touched\n"})
    A.REPO, A.FRONTIER_LEDGER = d, d / "frontier.jsonl"
    try:
        ok, why, facts = A.judge_frontier("dsh/topic", suite_at=lambda r, t: 1, merge_tree=ok_mt,
                                          check_after=ok_chk)
        A.record_frontier("dsh/topic", False, why, facts)
        row = json.loads((d / "frontier.jsonl").read_text().splitlines()[-1])
        check("every decision is recorded, WITH who it would have gone to",
              row["branch"] == "dsh/topic" and row["merged"] is False and row["would_have_gone_to"] == "claude",
              str(row))
    finally:
        A.REPO, A.FRONTIER_LEDGER = real_repo, real_ledger

    # (1) THE TICK MUST MEET ONE. Claude's review: main() only enumerated reviewq.pending(), which is
    # coder/brief-*, so the frontier judge would never have run on its own.
    d = repo_with({"scripts/tests/test_new.py": "def test_y():\n    assert 1\n"})
    g(d, "checkout", "-q", "main")
    g(d, "merge", "-q", "--no-ff", "-m", "merge topic", "dsh/topic")     # a merged frontier ref
    g(d, "checkout", "-qb", "claude/pending")
    (d / "MORE.md").write_text("more docs\n")
    g(d, "add", "-A")
    g(d, "commit", "-qm", "a pending claude change")
    g(d, "checkout", "-q", "main")
    g(d, "checkout", "-qb", "coder/brief-abc")
    (d / "x.py").write_text("x\n")
    g(d, "add", "-A")
    g(d, "commit", "-qm", "a coder build")
    g(d, "checkout", "-q", "main")
    A.REPO = d
    try:
        got = A.frontier_branches()
    finally:
        A.REPO = real_repo
    check("the tick can FIND a frontier branch without being told",
          got == ["claude/pending"], str(got))
    check("  and skips one already merged, and one that is not frontier",
          "dsh/topic" not in got and "coder/brief-abc" not in got, str(got))

    # (2) F6 IS "NO NEW FAILURE", not "no failure": main has standing FAILs, and a condition nothing can
    # satisfy is a block wearing one.
    check("a merged tree inheriting main's failures is acceptable",
          A.new_failures({"ceiling", "unrecorded"}, {"ceiling", "unrecorded"}) == [], "")
    check("  adding one failure is not", A.new_failures({"ceiling"}, {"ceiling", "hub page"}) == ["hub page"], "")
    check("  fixing one while inheriting nothing is fine",
          A.new_failures({"a", "b"}, {"a"}) == [], "")

    src = (ROOT / "scripts" / "auto_merge.py").read_text(encoding="utf-8")
    check("  the counter is written AFTER the merge, with the real result",
          "record_frontier(b, bool(done)" in src, "still recording before the merge")
    # A BRANCH IN A WORKTREE IS BEING WORKED ON (Claude's review of the merged frontier gate).
    d = repo_with({"scripts/tests/test_new.py": "def test_y():\n    assert 1\n"})
    g(d, "checkout", "-q", "main")
    g(d, "checkout", "-qb", "claude/in-flight")
    (d / "WIP.md").write_text("half done\n")
    g(d, "add", "-A")
    g(d, "commit", "-qm", "work in progress")
    g(d, "checkout", "-q", "main")
    wt = Path(tempfile.mkdtemp()) / "wt"
    assert g(d, "worktree", "add", str(wt), "claude/in-flight").returncode == 0
    A.REPO = d
    try:
        got = A.frontier_branches()
        in_use = A.worktree_branches()
    finally:
        A.REPO = real_repo
    check("a branch checked out in a worktree is NOT judged -- it is half-finished work",
          "claude/in-flight" not in got, str(got))
    check("  and the worktree listing sees it", "claude/in-flight" in in_use, str(in_use))
    check("  the main checkout's own branch is listed too, so nothing is exempt by accident",
          "main" in in_use, str(in_use))

    check("only dsh/ and claude/ are frontier branches",
          A.frontier_shape("dsh/x") and A.frontier_shape("claude/x") and not A.frontier_shape("coder/brief-1"),
          "frontier_shape is too broad")
    print("test_auto_merge_frontier:", "FAILED" if fails else "ok")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
