#!/usr/bin/env python3
"""mf5 oracle: do not track files that services write.

Seen: workspace/gate-runs.jsonl was committed while the hook appends to it on every run,
so no merge could land. The fix is a registry (workspace/runtime-files.json) with three
consumers: pre-commit refuses to stage a registered file, and check_workspace verifies
each entry is gitignored and untracked.

Pinned as behaviour against fixture git repos in a temp dir; the live repo's own registry
must exist and verify clean. RED before the fix: scripts/runtime_files.py does not exist,
there is no registry, and check_workspace has no runtime-files check.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

FAILS = []


def check(label, cond, detail=""):
    line = "  [%s] %s" % ("ok" if cond else "BAD", label)
    if not cond and detail:
        line += " -- " + detail
    print(line)
    if not cond:
        FAILS.append(label)


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True,
                          timeout=60)


def fixture(td, *, gitignored, tracked):
    repo = Path(td) / ("fx-" + ("gi" if gitignored else "ngi") + ("-tr" if tracked else ""))
    (repo / "workspace").mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "fixture")
    (repo / "workspace" / "runtime-files.json").write_text(json.dumps(
        {"files": ["workspace/gate-runs.jsonl", "workspace/run-logs/"]}), encoding="utf-8")
    (repo / "workspace" / "gate-runs.jsonl").write_text('{"run":1}\n', encoding="utf-8")
    if gitignored:
        (repo / ".gitignore").write_text("workspace/gate-runs.jsonl\nworkspace/run-logs/\n",
                                         encoding="utf-8")
    if tracked:
        git(repo, "add", "-f", "workspace/gate-runs.jsonl")
        git(repo, "commit", "-q", "-m", "fixture: track the runtime file")
    return repo


def main():
    print("mf5 oracle: runtime files are registered, unstaged and gitignored\n")
    try:
        import runtime_files
    except ImportError as e:
        check("scripts/runtime_files.py exists and imports", False, str(e))
        print("\n%d failed" % len(FAILS))
        return 1
    check("scripts/runtime_files.py exists and imports", True)

    with tempfile.TemporaryDirectory() as td:
        clean = fixture(td, gitignored=True, tracked=False)
        check("a gitignored, untracked entry verifies clean",
              runtime_files.problems(clean) == [],
              "%r" % (runtime_files.problems(clean),))

        tracked = fixture(td, gitignored=False, tracked=True)
        probs = runtime_files.problems(tracked)
        check("a TRACKED runtime file is a finding (the gate-runs.jsonl defect)",
              any(e == "workspace/gate-runs.jsonl" and "TRACKED" in w for e, w in probs),
              "%r" % (probs,))

        unignored = fixture(td, gitignored=False, tracked=False)
        probs = runtime_files.problems(unignored)
        check("an entry that is not gitignored is a finding too",
              any("not gitignored" in w for _, w in probs), "%r" % (probs,))

        hits = runtime_files.staged_runtime(
            ["workspace/gate-runs.jsonl", "scripts/x.py", "workspace/run-logs/tick.json"],
            clean)
        check("check-staged flags a registered file and a registered directory's child",
              hits == ["workspace/gate-runs.jsonl", "workspace/run-logs/tick.json"],
              "%r" % (hits,))
        check("  ...and leaves ordinary files alone", "scripts/x.py" not in hits)

        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "runtime_files.py"),
                            "check-staged", "workspace/gate-runs.jsonl"],
                           capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        check("the CLI refuses a staged registered file (exit 1, naming it)",
              r.returncode == 1 and "gate-runs" in r.stdout, f"rc={r.returncode}")

        # check_workspace carries the verdict.
        import check_workspace
        has = hasattr(check_workspace.Checker, "check_runtime_files")
        check("check_workspace has a runtime-files check", has)
        if has:
            c = check_workspace.Checker(tracked)
            c.check_runtime_files()
            check("  ...it FAILS on the fixture with the tracked runtime file",
                  "Registered runtime files are gitignored and untracked"
                  in getattr(c, "failed_names", []),
                  "failures=%r" % (getattr(c, "failed_names", []),))
            c2 = check_workspace.Checker(clean)
            before = c2.failures
            c2.check_runtime_files()
            check("  ...and PASSES on the clean fixture", c2.failures == before,
                  "failed_names=%r" % (getattr(c2, "failed_names", []),))

    # The live repo: the registry exists, names the gate-runs ledger, and verifies clean.
    reg = ROOT / "workspace" / "runtime-files.json"
    check("the live registry workspace/runtime-files.json exists", reg.is_file())
    if reg.is_file():
        entries = json.loads(reg.read_text(encoding="utf-8")).get("files", [])
        check("  ...and it names workspace/gate-runs.jsonl (the seen defect)",
              "workspace/gate-runs.jsonl" in entries, "%r" % (entries,))
        check("  ...and the live repo verifies clean", runtime_files.problems(ROOT) == [],
              "%r" % (runtime_files.problems(ROOT),))

    pc = (ROOT / "scripts" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    check("pre-commit is wired to refuse staged runtime files",
          "runtime_files" in pc and "check-staged" in pc)  # text-assert: the hook is shell; the wiring line is the only mechanical pin available

    n = len(FAILS)
    print("\n%d failed -- %s" % (n, "runtime files cannot be committed"
                                 if not n else "mf5 is not in place"))
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
