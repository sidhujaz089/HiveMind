"""coder.write_branch commits ONLY the files its patch touched -- a ledger that changed in the worktree
while the run was in it does not ride along (32 builds since 10-12 were ACCEPTED and NOT WRITTEN when the
commit gate refused such a stray file)."""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "projects" / "local-inference"))
import coder  # noqa: E402

PASSED = FAILED = 0


def report(name, got, want):
    global PASSED, FAILED
    ok = got == want
    PASSED += ok
    FAILED += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))


def git(*a, cwd):
    return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, timeout=60)


home = Path.home() / ".cache" / "hivemind-tmp"
home.mkdir(parents=True, exist_ok=True)
repo = Path(tempfile.mkdtemp(prefix="coder-commit-only-", dir=home))
git("init", "-q", "-b", "main", cwd=repo)
git("config", "user.email", "t@t", cwd=repo)
git("config", "user.name", "t", cwd=repo)
(repo / "a.py").write_text("x = 1\n")
(repo / "ledger.jsonl").write_text("{}\n")
git("add", "-A", cwd=repo)
git("commit", "-q", "-m", "base", cwd=repo)

saved = (coder.REPO, coder.apply_ops)
real_apply = coder.apply_ops


def apply_and_dirty(wt, ops):
    ok, d = real_apply(wt, ops)
    (Path(wt) / "ledger.jsonl").write_text("{}\n{\"written\": \"by a service mid-run\"}\n")
    return ok, d


coder.REPO, coder.apply_ops = repo, apply_and_dirty
try:
    ops = [{"path": "a.py", "old": "x = 1\n", "new": "x = 2\n"}]
    ok, branch, detail = coder.write_branch("t", ops, "m")
    report("the change is written to a branch", ok, True)
    files = git("show", "--name-only", "--format=", branch, cwd=repo).stdout.split()
    report("the commit carries only the patched file", files, ["a.py"])
    ok2, _b, why = coder.write_branch("t2", [], "m")
    report("a patch naming no file delivers nothing", (ok2, "names no file" in why), (False, True))
finally:
    coder.REPO, coder.apply_ops = saved
    subprocess.run(["git", "-C", str(repo), "worktree", "prune"], capture_output=True, timeout=60)
    import shutil
    shutil.rmtree(repo, ignore_errors=True)
print(f"\n{PASSED} passed, {FAILED} failed")
sys.exit(1 if FAILED else 0)
