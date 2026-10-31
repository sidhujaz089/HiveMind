#!/usr/bin/env python3
"""Phase 0b (2026-10-27, L2): a merge into main that touches a guarded file needs a second signature.

WHY. 06-prompts-final.md makes six files two-signature -- memory_guard, auto_merge, hooks,
role_gate, taskstore, dispatch_loop -- and until now nothing read that sentence. Four lanes merge
into one main: in the hour before this was written, L3 merged a dispatcher change and asked for its
signature AFTERWARDS ([895327], its own wording: "I merged before asking, which was wrong"), and L1
posted three branches as "awaiting DSH second signature" with nothing that would refuse them. A rule
that lives in prose is a rule that gets skipped by whoever is busiest.

WHAT IT ENFORCES, and where:
  * a MERGE COMMIT onto main (git merge --no-ff) whose branch touches a guarded file must carry
    `Reviewed-by: <tool>/<model> (<scope>)`, scope one of diff|oracle|suite-run|live;
  * the reviewer's model FAMILY must differ from the author's -- DeepSeek signing DeepSeek is the
    same model agreeing with itself, which is not a second signature (AGENTS.md, "A note on
    agreement"). THE AUTHOR IS WHO WROTE THE COMMITS THE MERGE BRINGS, not who ran `git merge`:
    reading the merger off the merge commit made the gate refuse its own author's branch ([0c0d20],
    2026-10-27) and let a family sign its own work as long as somebody else ran the command.
    A branch that mixes families needs a review from each of them.
  * a FAST-FORWARD onto main that brings a guarded file is REFUSED outright, because a fast-forward
    creates no commit and so has nowhere to carry a signature. That is the bypass a merge-only rule
    would leave open, and it is the same reasoning role_gate already uses for coder/* and bridge/*.

WHAT IT DELIBERATELY DOES NOT DO: it is not a tax on every merge. A branch that touches none of the
six files merges exactly as it does today, with no trailer. The refusal names the file that required
the signature, so the fix is mechanical.

A FIXTURE REPOSITORY, never this one: the shipped commit-msg and reference-transaction hooks, the
shipped role_gate.py, and a ROLES.json giving one Claude role and one DeepSeek role merge_review.
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
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, env=e,
                          timeout=120)


def msg(subject, agent, reviewed=None):
    out = subject + "\n\n"
    if reviewed:
        out += reviewed + "\n"
    return out + f"Agent: {agent}\n"


def fixture(tmp, guarded="scripts/role_gate.py",
            branch_agents=("claude-code/opus-5.5",), main_also_changes=()):
    r = Path(tmp) / "repo"
    (r / "scripts" / "hooks").mkdir(parents=True)
    (r / "scripts" / "tests").mkdir(parents=True)
    for h in ("commit-msg", "reference-transaction"):
        shutil.copy2(REPO / "scripts" / "hooks" / h, r / "scripts" / "hooks" / h)
    shutil.copy2(REPO / "scripts" / "role_gate.py", r / "scripts" / "role_gate.py")
    roles = json.loads((REPO / ROLES_REL).read_text(encoding="utf-8"))
    T = lambda *ks: [{"task": k, "interrupt": False} for k in ks]
    roles["roles"]["claude"]["tasks"] = T("merge_review", "direct_commit", "done_check")
    roles["roles"]["dsh"]["tasks"] = T("merge_review", "direct_commit", "console")
    roles.pop("lanes", None)                     # commit B's map is L1's test, not this one's
    (r / ROLES_REL).parent.mkdir(parents=True)
    (r / ROLES_REL).write_text(json.dumps(roles, indent=1), encoding="utf-8")
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "core.hooksPath", "scripts/hooks")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", msg("chore(workspace): base", "claude-code/opus-5.5"))
    # the branch that carries a guarded change
    git(r, "checkout", "-q", "-b", "l3/topic")
    p = r / guarded
    p.parent.mkdir(parents=True, exist_ok=True)
    for i, agent in enumerate(branch_agents):        # one commit per family, for the mixed cases
        p.write_text(f"# changed by the branch, commit {i}\n", encoding="utf-8")
        git(r, "add", "-A")
        # --no-verify: this fixture is a PLAIN repo, and role_gate refuses an AGENT-role commit on a
        # non-main branch outside a worktree (2026-10-09, its own rule and its own test). A real lane
        # makes this commit in a worktree. Bypassed here so the fixture can represent a branch written
        # by the other model family at all; the MERGE below still runs every hook.
        git(r, "commit", "-q", "--no-verify", "-m",
            msg("fix(workspace): a guarded change", agent))
    git(r, "checkout", "-q", "main")
    (r / "unrelated.txt").write_text("main moves\n", encoding="utf-8")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", msg("chore(workspace): move main", "claude-code/opus-5.5"))
    # Other lanes' GUARDED work lands on main while this branch waits -- the stale-branch case. It
    # has to arrive as a reviewed --no-ff merge: a DIRECT commit on main that touches a guarded file
    # is refused at ref-update as "a fast-forward would change a two-signature file", because the
    # ref-update rule only reads a signature off a commit with three parents (2026-10-27, found
    # building this case; the refusal's wording is wrong for a direct commit and is noted on the
    # board, not fixed here).
    if main_also_changes:
        git(r, "checkout", "-q", "-b", "other/topic")
        for q in main_also_changes:
            f = r / q
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("# another lane's guarded change, landed on main\n", encoding="utf-8")
        git(r, "add", "-A")
        git(r, "commit", "-q", "--no-verify", "-m",
            msg("fix(workspace): another lane's guarded change", "claude-code/opus-5.5"))
        git(r, "checkout", "-q", "main")
        git(r, "merge", "--no-ff", "-m",
            msg("feat(workspace): merge another lane's guarded change",
                "claude-code/opus-5.5",
                "Reviewed-by: dsh-harness/deepseek-v4.1-flash (suite-run)"), "other/topic")
    return r


def merge(r, author, reviewed=None, ff=False):
    args = ["merge"] + (["--ff-only"] if ff else ["--no-ff"]) + [
        "-m", msg("feat(workspace): merge l3/topic", author, reviewed), "l3/topic"]
    return git(r, *args)


def direct(r, path, agent, reviewed=None):
    """A commit made straight on main -- a revert or an emergency fix -- not a merge."""
    p = r / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# a direct change on main\n", encoding="utf-8")
    git(r, "add", "-A")
    return git(r, "commit", "-m", msg("Revert changes to a guarded file", agent, reviewed))


def merged(r):
    return len(git(r, "rev-list", "--parents", "-n1", "HEAD").stdout.split()) == 3


def main():
    # A GUARDED FILE, MERGED AS A FAST-FORWARD: no commit, so nowhere to put a signature.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/role_gate.py")
        p = git(r, "merge", "--ff-only", "l3/topic")
        report("a fast-forward onto main that brings a guarded file is refused", p.returncode != 0,
               p.stdout + p.stderr)

    # A MERGE COMMIT WITHOUT A SIGNATURE.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="projects/app-repo/taskstore.py")
        p = merge(r, "claude-code/opus-5.5")
        report("a merge commit touching a guarded file without Reviewed-by is refused",
               p.returncode != 0 and not merged(r), p.stdout + p.stderr)
        report("  and the refusal names the file that needed the signature",
               "projects/app-repo/taskstore.py" in (p.stdout + p.stderr), p.stdout + p.stderr)
        report("  and says what the trailer must look like",
               "Reviewed-by:" in (p.stdout + p.stderr), p.stdout + p.stderr)

    # THE SAME FAMILY IS NOT A SECOND SIGNATURE -- and the AUTHOR is who WROTE the commits, not who
    # ran git. A Claude-authored branch signed by Claude and merged by DeepSeek is one family
    # agreeing with itself: an independent family never read it.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py")
        p = merge(r, "dsh-harness/deepseek-v4.1-flash", "Reviewed-by: claude-code/opus-5.5 (suite-run)")
        report("a Claude-authored branch signed only by Claude is refused, even merged by DeepSeek",
               p.returncode != 0 and "model" in (p.stdout + p.stderr).lower(), p.stdout + p.stderr)

    # [0c0d20] -- L3's abort at 13:18Z, as a regression: the branch is DeepSeek's, the review is
    # Claude's, and a Claude instance runs the merge. The gate used to read the Claude line off the
    # MERGE commit, call it the author, and refuse a legitimate signature -- which forbade L2's
    # branch being merged by any Claude at all.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py",
                    branch_agents=("dsh-harness/deepseek-v4.1-flash",))
        p = merge(r, "claude-code/opus-5.5", "Reviewed-by: claude-code/opus-5.5 (suite-run)")
        report("a DeepSeek-authored branch signed by Claude merges when Claude runs the merge ([0c0d20])",
               p.returncode == 0 and merged(r), p.stdout + p.stderr)

    # A DIFFERENT FAMILY SIGNATURE, WITH A REAL SCOPE.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py")
        p = merge(r, "claude-code/opus-5.5", "Reviewed-by: dsh-harness/deepseek-v4.1-flash (suite-run)")
        report("a different-family Reviewed-by with a valid scope lets the merge through",
               p.returncode == 0 and merged(r), p.stdout + p.stderr)

    # A BRANCH THAT MIXES FAMILIES: one signature cannot cover both, because the family that wrote
    # half of it needs someone else to read that half.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py",
                    branch_agents=("claude-code/opus-5.5", "dsh-harness/deepseek-v4.1-flash"))
        p = merge(r, "claude-code/opus-5.5", "Reviewed-by: claude-code/opus-5.5 (suite-run)")
        report("a mixed-family branch with ONE signature is refused -- a family is left unreviewed",
               p.returncode != 0, p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py",
                    branch_agents=("claude-code/opus-5.5", "dsh-harness/deepseek-v4.1-flash"))
        p = merge(r, "claude-code/opus-5.5",
                  "Reviewed-by: claude-code/opus-5.5 (diff)\n"
                  "Reviewed-by: dsh-harness/deepseek-v4.1-flash (suite-run)")
        report("a mixed-family branch with one signature PER family is allowed",
               p.returncode == 0 and merged(r), p.stdout + p.stderr)

    # AN EMERGENCY REVERT IS NOT A FAST-FORWARD (L1's handover, 2026-10-27). A direct commit on main
    # that changes a guarded file was refused as "this fast-forward would change a two-signature
    # file" -- false, and unactionable: the commit HAS a message, so a signature can live in it. The
    # hook now tells the two apart by asking whether any OTHER branch points at the new commit.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py")
        before = git(r, "rev-parse", "HEAD").stdout.strip()
        p = direct(r, "scripts/auto_merge.py", "claude-code/opus-5.5")
        both = p.stdout + p.stderr
        report("a direct commit changing a guarded file with NO review is refused",
               p.returncode != 0 and git(r, "rev-parse", "HEAD").stdout.strip() == before, both)
        # the refusal says "this is not a fast-forward" on purpose, so assert on the ADVICE that
        # belongs to a fast-forward instead: a direct commit must not be told to merge with --no-ff.
        report("  and it is not given a fast-forward's instruction -- it has a message to sign",
               "DIRECT commit" in both and "A fast-forward creates no commit" not in both
               and "Reviewed-by" in both, both)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py")
        before = git(r, "rev-parse", "HEAD").stdout.strip()
        p = direct(r, "scripts/auto_merge.py", "claude-code/opus-5.5",
                   "Reviewed-by: dsh-harness/deepseek-v4.1-flash (suite-run)")
        report("a direct commit changing a guarded file WITH a second signature is allowed -- the "
               "emergency revert path",
               p.returncode == 0 and git(r, "rev-parse", "HEAD").stdout.strip() != before,
               p.stdout + p.stderr)

    # A SIGNATURE MAY NAME MORE THAN ONE SCOPE. L3 wrote "(diff + suite-run)" -- a truer claim than
    # either term alone -- and REVIEW_RE refused it, so a correct signature looked like a missing one.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py",
                    branch_agents=("dsh-harness/deepseek-v4.1-flash",))
        p = merge(r, "claude-code/opus-5.5", "Reviewed-by: claude-code/opus-5.5 (diff + suite-run)")
        report("a Reviewed-by naming TWO scopes parses (diff + suite-run)",
               p.returncode == 0 and merged(r), p.stdout + p.stderr)

    # PREFLIGHT: the decision BEFORE git touches anything. L3's refusal left 14 files staged under
    # an unchanged HEAD, because reference-transaction runs after the index and working tree moved.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py",
                    branch_agents=("dsh-harness/deepseek-v4.1-flash",))
        before = git(r, "rev-parse", "HEAD").stdout.strip()
        good = Path(tmp) / "good.txt"
        good.write_text(msg("feat(workspace): merge l3/topic", "claude-code/opus-5.5",
                            "Reviewed-by: claude-code/opus-5.5 (suite-run)"), encoding="utf-8")
        bad = Path(tmp) / "bad.txt"
        bad.write_text(msg("feat(workspace): merge l3/topic", "claude-code/opus-5.5"), encoding="utf-8")
        run = lambda m: subprocess.run(
            [sys.executable, str(r / "scripts" / "role_gate.py"), "preflight", "l3/topic", str(m)],
            cwd=str(r), capture_output=True, text=True)
        ok, refused = run(good), run(bad)
        report("preflight passes a merge that would be allowed, and merges nothing",
               ok.returncode == 0 and git(r, "rev-parse", "HEAD").stdout.strip() == before
               and not git(r, "status", "--porcelain").stdout.strip(), ok.stdout + ok.stderr)
        report("preflight refuses a merge that would be refused, and leaves the tree clean",
               refused.returncode != 0 and "Reviewed-by" in (refused.stdout + refused.stderr)
               and git(r, "rev-parse", "HEAD").stdout.strip() == before
               and not git(r, "status", "--porcelain").stdout.strip(), refused.stdout + refused.stderr)

    # A SCOPE OUTSIDE THE VOCABULARY IS NOT A SCOPE.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py")
        p = merge(r, "dsh-harness/deepseek-v4.1-flash", "Reviewed-by: claude-code/opus-5.5 (looked)")
        report("a Reviewed-by whose scope is not diff|oracle|suite-run|live is refused",
               p.returncode != 0, p.stdout + p.stderr)

    # A STALE BRANCH IS NOT CHARGED FOR MAIN'S FILES (L4's refusal (2), 2026-10-27). check_review
    # diffed HEAD..MERGE_HEAD, which lists a guarded file MAIN changed as if the branch had touched
    # it -- so a docs branch that had fallen behind a role_gate merge needed a signature for work it
    # never wrote, and a real finding was buried in files the branch never opened.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="docs/NOTES.md", main_also_changes=("scripts/auto_merge.py",))
        p = merge(r, "claude-code/opus-5.5")
        report("a branch that touches nothing guarded needs no signature, even when main changed one",
               p.returncode == 0 and merged(r), p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py",
                    main_also_changes=("projects/app-repo/taskstore.py",))
        p = merge(r, "claude-code/opus-5.5")
        both = (p.stdout + p.stderr)
        # only the CHARGED files: the refusal's footer lists all six guarded files by name, so the
        # assertion has to look at the block above it or it fails on the footer.
        charged = both.split("The commits this merge brings were written by")[0]
        report("  the refusal names the file the BRANCH changed, and not the one main changed",
               p.returncode != 0 and "scripts/dispatch_loop.py" in charged
               and "projects/app-repo/taskstore.py" not in charged, both)

    # THE MODEL IS THE SIGNER, NOT THE TOOL (Jas, 2026-10-27: "get rid of the word family"). The
    # tool before "/" used to be compared, so every ollama/* model was one signer and one model under
    # two harnesses was two.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py", branch_agents=("ollama/qwen3.8:27b",))
        p = merge(r, "claude-code/opus-5.5", "Reviewed-by: ollama/llama4:70b (suite-run)")
        report("two different models under the same tool may sign each other's work",
               p.returncode == 0 and merged(r), p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="scripts/dispatch_loop.py", branch_agents=("dsh-harness/opus-5.5",))
        p = merge(r, "dsh-harness/deepseek-v4.1-flash", "Reviewed-by: claude-code/opus-5.5 (suite-run)")
        report("one model under two harnesses is NOT a second signature",
               p.returncode != 0 and not merged(r), p.stdout + p.stderr)

    # NO MERGE BASE IS NOT A FREE PASS. check_review returned 0 when git found no merge base, so an
    # unrelated-history merge brought a guarded file with no signature (L3's handover, 2026-10-27).
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="docs/NOTES.md")
        git(r, "checkout", "-q", "--orphan", "stray")
        git(r, "rm", "-rq", "--cached", ".")
        (r / "scripts" / "auto_merge.py").write_text("# from an unrelated history\n", encoding="utf-8")
        git(r, "add", "scripts/auto_merge.py")
        git(r, "commit", "-q", "--no-verify", "-m", msg("feat(workspace): stray", "claude-code/opus-5.5"))
        git(r, "checkout", "-q", "-f", "main")
        p = git(r, "merge", "--no-ff", "--allow-unrelated-histories", "-X", "theirs",
                "-m", msg("feat(workspace): merge stray", "claude-code/opus-5.5"), "stray")
        report("an unrelated-history merge bringing a guarded file, unsigned, is refused",
               p.returncode != 0 and "scripts/auto_merge.py" in (p.stdout + p.stderr), p.stdout + p.stderr)

    # NO GUARDED FILE: NO TAX. The rule must not become a trailer on every merge in the repository.
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, guarded="docs/NOTES.md")
        p = merge(r, "claude-code/opus-5.5")
        report("a merge touching none of the six files needs no signature at all",
               p.returncode == 0 and merged(r), p.stdout + p.stderr)
        p = git(r, "merge", "--ff-only", "l3/topic~0")
        report("  and a fast-forward of an unguarded branch is still allowed",
               p.returncode in (0, 1), p.stdout + p.stderr)   # already merged above: 0 or no-op

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
