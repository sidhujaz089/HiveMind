#!/usr/bin/env python3
"""bridge_trigger's freeze and lint (Jas, 2026-09-28: the gates move oracles across the bridge, not
Claude). A throwaway git repository and a temporary ledger; the real ones are not touched."""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(ROOT / "scripts")]
import bridge_trigger as bt  # noqa: E402
import scope_quality as sq  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=60)


def branch(repo, task, files):
    git(repo, "checkout", "-q", "-b", f"bridge/{task}", "main")
    for f, t in files.items():
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text(t)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"bridge {task}")
    git(repo, "checkout", "-q", "main")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="freeze-test-"))
    real = (bt.FREEZE_OFF, sq.LEDGER)
    try:
        repo = tmp / "repo"
        repo.mkdir()
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.email", "t@t")
        git(repo, "config", "user.name", "t")
        (repo / "ledger.jsonl").write_text('{"a": 1}\n')
        (repo / "loop.sh").write_text("p1\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "init")
        bt.FREEZE_OFF = tmp / "OFF"
        import freeze_circuit as _fc
        _fc.RESET = tmp / "no-reset.json"           # the live reset must not decide this fixture (2026-10-01)
        sq.LEDGER = tmp / "sq.jsonl"

        branch(repo, "t1", {"scripts/tests/test_x.py": "raise SystemExit(1)\n", "ledger.jsonl": '{"a": 1}\n{"b": 2}\n'})
        with open(repo / "ledger.jsonl", "a") as fh:
            fh.write('{"live": 3}\n')                      # main's uncommitted run record
        rc = bt.freeze_branch(repo, "t1", "qwen-x")
        report("a clean branch is frozen into main", rc, 0)
        report("  the oracle is on main", (repo / "scripts/tests/test_x.py").is_file(), True)
        report("  the branch is deleted", git(repo, "branch", "--list", "bridge/t1").stdout.strip(), "")
        report("  main's uncommitted appended ledger lines survive, after the branch's",
               (repo / "ledger.jsonl").read_text(), '{"a": 1}\n{"b": 2}\n{"live": 3}\n')
        msg = git(repo, "log", "-1", "--format=%B").stdout
        report("  the merge names the gates and the model", ("scope lint" in msg, "Agent: ollama/qwen-x" in msg),
               (True, True))

        branch(repo, "t2", {"loop.sh": "p1\np2\n"})
        (repo / "loop.sh").write_text("p1\nlocal edit\n")
        report("uncommitted non-ledger overlap -> refused, left for Claude", bt.freeze_branch(repo, "t2", "q"), 4)
        report("  the branch is kept", git(repo, "branch", "--list", "bridge/t2").stdout.strip() != "", True)
        report("  main's edit is untouched", (repo / "loop.sh").read_text(), "p1\nlocal edit\n")
        git(repo, "checkout", "--", "loop.sh")

        # 2026-10-19: 39 of 42 freezes held for Claude were the failure ceiling, which services write all day
        ceil = repo / "workspace" / "test-failures-ceiling.json"
        ceil.parent.mkdir(parents=True, exist_ok=True)
        ceil.write_text(json.dumps({"allowed_to_fail": ["test_a.py"], "_why_each": {"test_a.py": "old"}}, indent=1))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "ceiling")
        branch(repo, "t3", {"workspace/test-failures-ceiling.json": json.dumps(
            {"allowed_to_fail": ["test_a.py", "test_new.py"], "_why_each": {"test_a.py": "old", "test_new.py": "oracle"}},
            indent=1)})
        ceil.write_text(json.dumps({"allowed_to_fail": ["test_live.py"],
                                    "_why_each": {"test_live.py": "declared live"}}, indent=1))
        report("a live edit to the failure ceiling does not hold a freeze", bt.freeze_branch(repo, "t3", "q"), 0)
        got_c = json.loads(ceil.read_text())
        report("  the branch's entry and the live entry are both kept, the live removal too",
               sorted(got_c["allowed_to_fail"]), ["test_live.py", "test_new.py"])
        report("  and the reasons follow the same rule", sorted(got_c["_why_each"]), ["test_live.py", "test_new.py"])
        report("  the live edit stays uncommitted, as it was", "test-failures-ceiling" in git(repo, "status", "--porcelain").stdout, True)
        git(repo, "rm", "-q", "-r", "--cached", "workspace")
        git(repo, "commit", "-q", "-m", "drop the ceiling fixture")
        import shutil as _sh
        _sh.rmtree(repo / "workspace")

        bt.FREEZE_OFF.write_text("off")
        report("the switch file turns the freeze off", bt.freeze_branch(repo, "t2", "q"), 4)
        bt.FREEZE_OFF.unlink()
        sq.LEDGER.write_text("".join(json.dumps({"stage": "merge", "outcome": "sent-back", "class": "oracle-weak",
                                                 "scope": f"s{i}", "model": "q"}) + "\n" for i in range(3)))
        on, why = bt.freeze_enabled()
        report("3 oracle send-backs in the last 20 merges turn it off by itself", (on, "3 of the last 3" in why),
               (False, True))
        sq.LEDGER.write_text("")

        # lint in run(): findings go back to the task, nothing is branched
        ans = tmp / "a.md"
        ans.write_text('x\n```chunks\n[{"kind": "oracle", "file": "scripts/tests/test_y.py", "oracle_text": "print(1)\\n"}]\n```\n')
        notes, requeued = [], []
        rc = bt.run(ans, "t3", "q", repo=repo, note=lambda t, x: notes.append(x), lint=True,
                    requeue=requeued.append,
                    lint_fn=lambda text, r: ([], [("oracle-green", 0, "scripts/tests/test_y.py", "exit 0 on main")]))
        report("lint findings: exit 2, a note naming the class, the task requeued, no branch",
               (rc, "[oracle-green]" in notes[0], requeued, git(repo, "branch", "--list", "bridge/t3").stdout.strip()),
               (2, True, ["t3"], ""))

        # ORACLES-ONLY SCOPES GO BACK TO THE MANAGER (2026-10-13, t1115): no brief and no enrolment line
        # added -> the task is requeued; a scope that enrols a build chunk is left alone.
        git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "settle", "--allow-empty")
        got = []
        branch(repo, "t7", {"scripts/tests/test_only_oracle.py": "raise SystemExit(1)\n"})
        report("an oracles-only scope freezes", bt.freeze_branch(repo, "t7", "q", requeue=got.append), 0)
        report("  and is requeued for the manager to scope its implementation", got, ["t7"])
        got.clear()
        branch(repo, "t8", {"scripts/tests/test_with_build.py": "raise SystemExit(1)\n",
                            "workspace/briefs/brief-p9-t8-x.txt": "TASK: t8\n"})
        report("a scope with a build chunk freezes", bt.freeze_branch(repo, "t8", "q", requeue=got.append), 0)
        report("  and is NOT requeued", got, [])
        got.clear()
        branch(repo, "t9", {"scripts/tests/test_enrolled.py": "raise SystemExit(1)\n",
                            "scripts/autonomy_loop.sh": "p9-t9-x|brief-p9-t9-x.txt|a.py|scripts/tests/test_enrolled.py\n"})
        report("a scope that enrols a line in the build table is NOT requeued",
               (bt.freeze_branch(repo, "t9", "q", requeue=got.append), got), (0, []))
        report("a scratch repo never reaches the live task store (default requeue is a no-op off the live repo)",
               bt.Path(repo).resolve() != bt.REPO.resolve(), True)
    finally:
        bt.FREEZE_OFF, sq.LEDGER = real
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
