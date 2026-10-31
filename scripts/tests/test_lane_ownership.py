#!/usr/bin/env python3
"""Commit B of the lanes board (2026-10-27): a lane commit may not touch another lane's file.

Jas, 2026-10-27: "commit B of the board -- `owns:` per lane in ROLES.json (validated in
role_store) plus a pre-commit refusal of a commit whose `Lane:` trailer does not own or hold an
unexpired claim on a touched owned/claimed file. Read the live board at its absolute path
(lanes.BOARD), never a worktree copy."

WHY THIS EXISTS. Four instances now write one repository from four worktrees. The board
(scripts/lanes.py, commit 0135c3e1e) records CLAIMS, but a claim is a promise: nothing read it.
The lane prompts say "claim a file before editing it" and a prompt is not a check (AGENTS.md rule
9's spirit -- a rule nobody can fail is not a rule). The lane prompts also name each lane's OWNED
files, six weeks of looser "YOUR LANE OWNS" text that no program could read. Both halves are now
data: `lanes.<Lx>.owns` in ROLES.json, validated by role_store.check(), enforced here.

WHAT IS REFUSED, and what is deliberately not:
  * a commit WITH a `Lane: Lx` trailer that touches a file another lane owns, unless this lane
    holds an unexpired claim on that file;
  * the same for a file another lane holds an unexpired CLAIM on, owned or not;
  * allowed: the owner's own files, files nobody owns or claims, and a file this lane claims.
  * NOT gated: a commit with no `Lane:` trailer (the services, Jas, and every model commit before
    2026-10-26 write one). The gate keys on the trailer, so a lane opts into the rule by naming
    itself -- the same choice commit-msg makes when it validates the trailer's shape only.

A FIXTURE REPOSITORY, NEVER THIS ONE, as test_role_gate.py does: it gets the real commit-msg and
role_gate.py, a ROLES.json carrying a `lanes` block, and a board in a throwaway LANES_HOME, so what
is tested is the shipped hooks acting on real git commits.
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


def msg(subject, agent, lane=None):
    out = subject + "\n\n"
    if lane:
        out += f"Lane: {lane}\n"
    return out + f"Agent: {agent}\n"


def fixture(tmp, lanes=None):
    """A repo with the shipped commit-msg, role_gate.py, role_store.py and lanes.py."""
    r = Path(tmp) / "repo"
    (r / "scripts" / "hooks").mkdir(parents=True)
    (r / "scripts" / "tests").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "hooks" / "commit-msg", r / "scripts" / "hooks" / "commit-msg")
    for f in ("role_gate.py", "role_store.py", "lanes.py"):
        shutil.copy2(REPO / "scripts" / f, r / "scripts" / f)
    roles = json.loads((REPO / ROLES_REL).read_text(encoding="utf-8"))
    # The task lists are pinned for the reason test_role_gate.py pins them: a Models-tab edit must
    # not be able to turn this suite red and hold every build.
    T = lambda *ks: [{"task": k, "interrupt": False, "teamwork": False} for k in ks]
    roles["roles"]["claude"]["tasks"] = T("merge_review", "direct_commit", "done_check")
    roles["roles"]["dsh"]["tasks"] = T("console", "merge_review", "direct_commit")
    roles["lanes"] = lanes if lanes is not None else {
        "L1": {"name": "Claude-GX10", "owns": ["scripts/memory_guard.py", "scripts/hooks/"]},
        "L2": {"name": "DSH-ThinkPad", "owns": ["scripts/l2_owned.py", "scripts/role_gate.py"]},
        "L3": {"name": "Claude-ThinkPad", "owns": ["scripts/dispatch_loop.py"]},
        "L4": {"name": "DSH-GX10", "owns": ["projects/app-repo/", "scripts/console.py"]},
    }
    (r / ROLES_REL).parent.mkdir(parents=True)
    (r / ROLES_REL).write_text(json.dumps(roles, indent=1), encoding="utf-8")
    # A board path of its own, so the suite can never read or write the live one.
    (r / "boardhome").mkdir()
    # ON MAIN, and with dsh holding direct_commit: check_commit's OLDER rule refuses an agent role
    # committing on a branch of a non-worktree checkout, and that rule is not what this suite tests.
    # main + direct_commit isolates the lane gate.
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "core.hooksPath", "scripts/hooks")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", msg("chore(workspace): base", "claude-code/opus-5.5"))
    return r


def env_for(r):
    return {"LANES_HOME": str(Path(r) / "boardhome")}


def write(r, path, text="x\n"):
    p = Path(r) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    git(r, "add", path)


def claim(r, lane, what, hours=None):
    args = ["claim", lane, what, "test claim"]
    if hours is not None:
        args += ["--hours", str(hours)]
    p = subprocess.run([sys.executable, str(Path(r) / "scripts" / "lanes.py"), *args],
                       capture_output=True, text=True, env=dict(os.environ, **env_for(r)),
                       timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr


def store_problems(r, lanes):
    """role_store.check()'s lane findings, computed on a store with these `lanes` and no chart."""
    code = ("import json,sys;sys.path.insert(0,%r);import role_store as R;"
            "d=R.load();d['lanes']=json.loads(sys.argv[1]);"
            "print(json.dumps(R.lane_problems(d)))") % str(Path(r) / "scripts")
    p = subprocess.run([sys.executable, "-c", code, json.dumps(lanes)],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    return json.loads(p.stdout)


def main():
    # ---- the store half: `owns:` is data, and role_store validates it ------------------------
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        clean = store_problems(r, {"L1": {"owns": ["scripts/memory_guard.py", "scripts/hooks/"]},
                                   "L2": {"owns": ["scripts/role_gate.py"]}})
        report("role_store.check() accepts a well-formed lanes block", clean == [], clean)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        probs = store_problems(r, {"L1": {"owns": ["scripts/x.py"]},
                                   "L2": {"owns": ["scripts/x.py"]}})
        report("two lanes owning the same file is a store problem, not a silent tie",
               len(probs) == 1 and "scripts/x.py" in probs[0] and "L1" in probs[0] and "L2" in probs[0],
               probs)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        probs = store_problems(r, {"L5": {"owns": ["scripts/x.py"]},
                                   "L2": {"owns": ["/abs/path.py", "../outside.py", ""]}})
        report("an unknown lane key is a problem", any("L5" in p for p in probs), probs)
        report("an absolute path, a path escaping the repo, and an empty entry are problems",
               sum(1 for p in probs if "/abs/path.py" in p or "outside" in p or "empty" in p) == 3,
               probs)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp, lanes={"L2": {"owns": ["projects/app-repo/taskstore.py"]},
                                "L4": {"owns": ["projects/app-repo/"]}})
        # A directory pattern and a longer exact path: the most specific owner wins, or L2's
        # taskstore.py (inside L4's projects/app-repo/) would belong to L4.
        code = ("import sys;sys.path.insert(0,%r);import role_store as R,json;"
                "d=R.load();"
                "print(json.dumps([R.owner_of(p,d) for p in "
                "['projects/app-repo/taskstore.py','projects/app-repo/hub.py','scripts/x.py']]))"
                ) % str(Path(r) / "scripts")
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        got = json.loads(p.stdout) if p.returncode == 0 else None
        report("owner_of gives the most specific pattern: taskstore.py is L2's, hub.py is L4's",
               got == [["L2", "projects/app-repo/taskstore.py"], ["L4", "projects/app-repo/"],
                       [None, None]], p.stdout + p.stderr)

    # ---- the commit half ---------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        write(r, "scripts/memory_guard.py")
        p = git(r, "commit", "-q", "-m", msg("fix(workspace): t", "dsh-harness/deepseek-v4.1-flash",
                                             "L2"), env=env_for(r))
        report("a lane commit touching ANOTHER lane's owned file is refused",
               p.returncode != 0, p.stdout + p.stderr)
        report("  and the refusal names the owner, the file and the lane that may not touch it",
               "L1" in (p.stdout + p.stderr) and "scripts/memory_guard.py" in (p.stdout + p.stderr)
               and "L2" in (p.stdout + p.stderr), p.stdout + p.stderr)
        report("  and nothing was committed",
               git(r, "rev-list", "--count", "HEAD").stdout.strip() == "1", "HEAD moved")

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        write(r, "scripts/l2_owned.py")
        p = git(r, "commit", "-q", "-m", msg("fix(workspace): t", "dsh-harness/deepseek-v4.1-flash",
                                             "L2"), env=env_for(r))
        report("the owner's own Lane trailer may touch its own file", p.returncode == 0,
               p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        write(r, "notes/anything.md")
        p = git(r, "commit", "-q", "-m", msg("docs(workspace): t", "dsh-harness/deepseek-v4.1-flash",
                                             "L3"), env=env_for(r))
        report("a file nobody owns is anyone's to touch", p.returncode == 0, p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        write(r, "scripts/memory_guard.py")
        p = git(r, "commit", "-q", "-m", msg("fix(workspace): t", "claude-code/opus-5.5"), env=env_for(r))
        report("a commit with NO Lane trailer is not gated (the services and Jas still commit)",
               p.returncode == 0, p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        claim(r, "L2", "scripts/memory_guard.py")
        write(r, "scripts/memory_guard.py")
        p = git(r, "commit", "-q", "-m", msg("fix(workspace): t", "dsh-harness/deepseek-v4.1-flash",
                                             "L2"), env=env_for(r))
        report("an unexpired CLAIM is how a lane borrows another lane's file", p.returncode == 0,
               p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        claim(r, "L2", "scripts/memory_guard.py", hours=0.0003)   # ~1 s
        import time
        time.sleep(1.2)
        write(r, "scripts/memory_guard.py")
        p = git(r, "commit", "-q", "-m", msg("fix(workspace): t", "dsh-harness/deepseek-v4.1-flash",
                                             "L2"), env=env_for(r))
        report("an EXPIRED claim does not authorise anything", p.returncode != 0, p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        claim(r, "L3", "notes/borrowed.md")                       # unowned, but claimed by L3
        write(r, "notes/borrowed.md")
        p = git(r, "commit", "-q", "-m", msg("docs(workspace): t", "dsh-harness/deepseek-v4.1-flash",
                                             "L2"), env=env_for(r))
        report("another lane's claim on an UNOWNED file is refused too", p.returncode != 0,
               p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        claim(r, "L2", "notes/mine.md")
        write(r, "notes/mine.md")
        p = git(r, "commit", "-q", "-m", msg("docs(workspace): t", "dsh-harness/deepseek-v4.1-flash",
                                             "L2"), env=env_for(r))
        report("a file the lane itself claims is allowed", p.returncode == 0, p.stdout + p.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        r = fixture(tmp)
        # The store is what makes the rule: an unowned file is free even to a lane that owns others.
        write(r, "scripts/hooks/pre-commit")
        p = git(r, "commit", "-q", "-m", msg("fix(workspace): t", "dsh-harness/deepseek-v4.1-flash",
                                             "L2"), env=env_for(r))
        report("a directory pattern owns everything under it", p.returncode != 0, p.stdout + p.stderr)

    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
