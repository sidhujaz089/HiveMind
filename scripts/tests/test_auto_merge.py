#!/usr/bin/env python3
"""auto_merge.judge(): merges only when every mechanical condition holds (Jas, 2026-10-01).

A throwaway git repo laid out like this one: a module, a red oracle, a brief naming SYMBOLS, a
work-list row. One clean branch must pass; each variant breaks exactly one condition and must be
refused for THAT reason. merge_gate (condition 6) is not run here -- it has its own suites.
"""
import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import auto_merge as A          # noqa: E402
import merge_accounting as MA   # noqa: E402

FAILED = 0
MOD = "def a():\n    return 1\n\n\ndef b():\n    return 1\n"
ORACLE = ("import sys\nsys.path.insert(0, '.')\nimport mod\nsys.exit(0 if mod.a() == 2 else 1)\n")


def report(label, got, want):
    global FAILED
    ok = got == want
    FAILED += not ok
    print(f"  [{'ok' if ok else 'BAD'}]  {label}: {got!r}" + ("" if ok else f"  (want {want!r})"))


def g(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, timeout=60)


def fixture():
    repo = Path(tempfile.mkdtemp(prefix="automerge-test-"))
    g(repo, "init", "-q", "-b", "main")
    g(repo, "config", "user.email", "t@t"); g(repo, "config", "user.name", "t")
    (repo / "scripts" / "tests").mkdir(parents=True)
    (repo / "workspace" / "briefs").mkdir(parents=True)
    (repo / "mod.py").write_text(MOD)
    (repo / "scripts" / "tests" / "test_x.py").write_text(ORACLE)
    brief = "TASK: t1\nSYMBOLS: a\nMake a() return 2.\nFROZEN ORACLE: scripts/tests/test_x.py\n"
    (repo / "workspace" / "briefs" / "brief-p1-x.txt").write_text(brief)
    (repo / "scripts" / "autonomy_loop.sh").write_text(
        "p1-x|brief-p1-x.txt|mod.py|scripts/tests/test_x.py\n")
    g(repo, "add", "-A"); g(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "base")
    return repo, "coder/brief-" + hashlib.sha256(brief.encode()).hexdigest()[:8]


def branch(repo, name, new_mod):
    g(repo, "checkout", "-q", "-b", name, "main")
    (repo / "mod.py").write_text(new_mod)
    g(repo, "-c", "core.hooksPath=/dev/null", "commit", "-qam", "build")
    g(repo, "checkout", "-q", "main")


def main():
    repo, label = fixture()
    saved = (A.REPO, MA.BRIEFS)
    A.REPO, MA.BRIEFS = repo, repo / "workspace" / "briefs"
    try:
        branch(repo, label, MOD.replace("return 1\n\n\ndef b", "return 2\n\n\ndef b"))
        ok, why, _ = A.judge(label, run_gate=False)
        report("a narrow red->green one-file build passes", (ok, why), (True, []))

        branch(repo, label + "-wide", MOD.replace("return 1\n\n\ndef b", "return 2\n\n\ndef b")
               .replace("def b():\n    return 1", "def b():\n    return 3"))
        ok, why, _ = A.judge(label + "-wide", run_gate=False)
        report("a hunk outside the named symbols is refused", (ok, "outside the named" in (why or [""])[0]), (False, True))

        branch(repo, label + "-name", MOD.replace("return 1\n\n\ndef b", "return helper()\n\n\ndef b"))
        ok, why, _ = A.judge(label + "-name", run_gate=False)
        report("a name bound nowhere is refused", (ok, "bound nowhere" in (why or [""])[0]), (False, True))

        branch(repo, label + "-gone", "def a():\n    return 2\n")
        ok, why, _ = A.judge(label + "-gone", run_gate=False)
        report("removing an unnamed definition is refused",
               (ok, any("removes top-level" in w or "outside the named" in w for w in why)), (False, True))

        branch(repo, label + "-red", MOD.replace("return 1\n\n\ndef b", "return 5\n\n\ndef b"))
        ok, why, _ = A.judge(label + "-red", run_gate=False)
        report("an oracle still red on the branch is refused", (ok, "does not pass" in (why or [""])[0]), (False, True))

        real_t = A.target_for
        A.target_for = lambda b, t: "scripts/tests/test_comply_mod_swallowed_exception.py"
        ok, why, _ = A.judge(label, run_gate=False)
        A.target_for = real_t
        report("a compliance build is never auto-merged (t341)", (ok, "compliance oracle" in (why or [""])[0]), (False, True))
    finally:
        A.REPO, MA.BRIEFS = saved
        shutil.rmtree(repo, ignore_errors=True)
    print("test_auto_merge:", "FAILED" if FAILED else "ok")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
