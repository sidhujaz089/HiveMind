#!/usr/bin/env python3
"""The script library: an unregistered script cannot run, a registered one can.

Jas, 2026-09-14: "Scripts can't run unless registered." Pinned in both directions, because a
guard that refuses everything and one that refuses nothing each pass half a fixture:
  * the runtime guard refuses an unregistered .py inside the repo, runs a registered one, and
    ignores files outside the repo, `-c` code, and library.py itself;
  * it fails CLOSED when the library is unreadable -- except library.py, so it can be repaired;
  * `library.py run` refuses unregistered scripts, .sh included;
  * `verify` catches both drifts: a script on disk that is not registered, and an entry whose
    file is gone.
The guard is exercised with runpy against a temporary repository tree, so this fixture does not
depend on the guard being installed and cannot lock the real repository while it runs.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PASSED = FAILED = 0


def report(label, got, want):
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [ok]   {label}: {got}")
    else:
        FAILED += 1
        print(f"  [BAD]  {label}: expected {want}, got {got}")


def guard_exit(repo, argv0):
    """Run the guard as if Python were starting argv0, with the guard living in repo/scripts/hooks."""
    # exec with an explicit __file__, NOT runpy.run_path: run_path sets sys.argv[0] to the file
    # it runs, so the guard saw ITSELF as the script being launched and refused everything.
    g = str(repo / "scripts" / "hooks" / "usercustomize.py")
    code = ("import sys; sys.argv=[%r]\n"
            "exec(compile(open(%r).read(), %r, 'exec'), {'__file__': %r, '__name__': 'usercustomize'})\n"
            "print('ALLOWED')") % (argv0, g, g, g)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return r.returncode, "ALLOWED" in r.stdout, r.stderr


def main():
    tmp = Path(tempfile.mkdtemp())
    repo = tmp / "ai-workspace"
    (repo / "scripts" / "hooks").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "hooks" / "usercustomize.py", repo / "scripts" / "hooks")
    shutil.copy(ROOT / "scripts" / "library.py", repo / "scripts")
    # register runs the compliance checks, so the checks travel with the library
    shutil.copy(ROOT / "scripts" / "library_checks.py", repo / "scripts")
    shutil.copy(ROOT / "scripts" / "signature_gate.py", repo / "scripts")
    # and so does the Flow chart: a permanent script must be on it to register (2026-10-01)
    shutil.copy(ROOT / "scripts" / "flow_coverage.py", repo / "scripts")
    (repo / "projects" / "local-inference" / "models").mkdir(parents=True)
    (repo / "projects" / "local-inference" / "models" / "FLOW.json").write_text(json.dumps(
        {"tasks": [{"id": "t", "name": "T", "worker": "x", "command": "x", "queue": "", "note": ""}],
         "links": [], "sources": [], "services": [],
         "helpers": [{"script": p, "serves": ["t"], "what": "fixture"} for p in
                     ("scripts/rogue.py", "scripts/timer.sh", "scripts/hooks/usercustomize.py",
                      "scripts/leaky.py", "scripts/good.py")]}))
    (repo / "workspace").mkdir()
    (repo / "scripts" / "good.py").write_text("print('hi')\n")
    (repo / "scripts" / "rogue.py").write_text("print('hi')\n")
    (repo / "scripts" / "timer.sh").write_text("echo hi\n")
    (tmp / "outside.py").write_text("print('hi')\n")
    lib = {"version": 1, "scripts": {"scripts/good.py": {"kind": "tool", "description": "a fixture"},
                                     "scripts/library_checks.py": {"kind": "lib", "description": "checks"},
                                     "scripts/signature_gate.py": {"kind": "tool", "description": "gate"},
                                     "scripts/library.py": {"kind": "tool", "description": "the library"}}}
    (repo / "scripts" / "library.json").write_text(json.dumps(lib))

    rc, ok, err = guard_exit(repo, str(repo / "scripts" / "rogue.py"))
    report("an unregistered script inside the repo is refused", (rc, ok), (3, False))
    report("and the refusal says how to register it", "library.py register scripts/rogue.py" in err, True)
    rc, ok, _ = guard_exit(repo, str(repo / "scripts" / "good.py"))
    report("a registered script runs", (rc, ok), (0, True))
    rc, ok, _ = guard_exit(repo, str(tmp / "outside.py"))
    report("a script outside the repo is untouched", (rc, ok), (0, True))
    rc, ok, _ = guard_exit(repo, "-c")
    report("`python3 -c` is untouched", (rc, ok), (0, True))

    # ---- A GIT WORKTREE IS THE SAME REPO (2026-09-22).
    # `repo` is derived from the guard's INSTALLED symlink, which points into the main
    # checkout, so a script under .claude/worktrees/<name>/ produced a rel path prefixed with
    # the worktree and matched nothing in library.json: EVERY script in EVERY worktree was
    # refused, which is how three changes ended up being exported as patches for a human to
    # apply. The fix maps the path back rather than exempting the directory, so the second
    # assertion here is the load-bearing one -- exempting would have made a worktree the one
    # place the library gate does not apply.
    wt = repo / ".claude" / "worktrees" / "wt" / "scripts"
    wt.mkdir(parents=True)
    (wt / "good.py").write_text("print('hi')\n")
    (wt / "rogue.py").write_text("print('hi')\n")
    rc, ok, _ = guard_exit(repo, str(wt / "good.py"))
    report("a registered script runs from inside a worktree", (rc, ok), (0, True))
    rc, ok, err = guard_exit(repo, str(wt / "rogue.py"))
    report("an unregistered script in a worktree is STILL refused", (rc, ok), (3, False))
    report("and the refusal names the real path, not the worktree one",
           "library.py register scripts/rogue.py" in err, True)

    # ---- AND IT READS THE WORKTREE'S OWN REGISTRY (2026-09-23). A real worktree carries
    # scripts/library.json. Reading main's meant a script registered on a branch was refused
    # on that branch, so a new test could not be run -- or shown failing -- where it was
    # written. A tree without a registry keeps main's (the cases above).
    (wt / "library.json").write_text(json.dumps({"scripts": {**lib["scripts"],
                                                             "scripts/branchonly.py": {}}}))
    (wt / "branchonly.py").write_text("print('hi')\n")
    rc, ok, _ = guard_exit(repo, str(wt / "branchonly.py"))
    report("a script registered only in the worktree runs there", (rc, ok), (0, True))
    rc, ok, _ = guard_exit(repo, str(wt / "rogue.py"))
    report("  and one registered nowhere is still refused", (rc, ok), (3, False))
    (repo / "scripts" / "branchonly.py").write_text("print('hi')\n")
    rc, ok, _ = guard_exit(repo, str(repo / "scripts" / "branchonly.py"))
    report("  and main does not inherit the branch's registration", (rc, ok), (3, False))

    (repo / "scripts" / "library.json").write_text("{ torn")
    rc, ok, err = guard_exit(repo, str(repo / "scripts" / "good.py"))
    report("an unreadable library fails CLOSED", (rc, ok), (3, False))
    rc, ok, _ = guard_exit(repo, str(repo / "scripts" / "library.py"))
    report("but library.py still runs, so it can be repaired", (rc, ok), (0, True))
    (repo / "scripts" / "library.json").write_text(json.dumps(lib))

    L = [sys.executable, str(repo / "scripts" / "library.py")]
    r = subprocess.run(L + ["run", "scripts/timer.sh"], capture_output=True, text=True, cwd=repo)
    report("`library.py run` refuses an unregistered .sh", r.returncode, 3)
    r = subprocess.run(L + ["run", "scripts/good.py"], capture_output=True, text=True, cwd=repo)
    report("`library.py run` runs a registered script", (r.returncode, r.stdout.strip()), (0, "hi"))

    r = subprocess.run(L + ["verify"], capture_output=True, text=True, cwd=repo)
    report("verify catches scripts on disk that are not registered",
           (r.returncode, "UNREGISTERED  scripts/rogue.py" in r.stdout), (1, True))
    r = subprocess.run(L + ["register", "scripts/rogue.py", ""], capture_output=True, text=True, cwd=repo)
    report("registering without a description is refused", r.returncode, 1)
    for f in ("scripts/rogue.py", "scripts/timer.sh", "scripts/hooks/usercustomize.py"):
        subprocess.run(L + ["register", f, "fixture"], capture_output=True, text=True, cwd=repo)
    (repo / "scripts" / "offchart.py").write_text("print('hi')\n")
    r = subprocess.run(L + ["register", "scripts/offchart.py", "fixture"], capture_output=True, text=True, cwd=repo)
    report("a script that is not on the Flow chart is refused",
           (r.returncode, "not on the Flow chart" in r.stdout), (1, True))
    (repo / "scripts" / "offchart.py").unlink()
    (repo / "scripts" / "good.py").unlink()
    r = subprocess.run(L + ["verify"], capture_output=True, text=True, cwd=repo)
    report("verify catches an entry whose file is gone",
           (r.returncode, "STALE ENTRY   scripts/good.py" in r.stdout), (1, True))
    report("registering regenerates SCRIPTS.md", (repo / "workspace" / "SCRIPTS.md").exists(), True)
    (repo / "scripts" / "leaky.py").write_text('row["parse_error"] = answer[:400]\n')
    r = subprocess.run(L + ["register", "scripts/leaky.py", "fixture"], capture_output=True, text=True, cwd=repo)
    report("registration runs the checks and refuses a blocking defect",
           (r.returncode, "truncates-record" in r.stdout), (1, True))
    r = subprocess.run(L + ["register", "scripts/leaky.py", "fixture", "--accept", "fixture only"],
                       capture_output=True, text=True, cwd=repo)
    report("an accepted exception registers, with its reason recorded",
           (r.returncode, json.loads((repo / "scripts" / "library.json").read_text())["scripts"]
            .get("scripts/leaky.py", {}).get("accepted")), (0, "fixture only"))
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
