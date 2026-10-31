#!/usr/bin/env python3
"""coder_harness.build_clone: the copy under test is the WORKING TREE, it is never made in RAM, and
carrying the tree across does not depend on applying a patch. Fixture repo only -- no model, no coder.py.

2026-10-30: four coder suites, killed at the flake hunt's 600 s cap, left 1.5 GB clones in tmpfs /tmp
(6.2 GB) with `git apply -` of the live tree's 109 MB diff still running under PID 1.

Run: python3 scripts/tests/test_coder_harness_clone.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coder_harness as H  # noqa: E402


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout


def fixture(root):
    repo = Path(root) / "src"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "kept.py").write_text("x = 1\n")
    (repo / "gone.py").write_text("y = 2\n")
    (repo / "blob.bin").write_bytes(bytes(range(256)))
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    (repo / "kept.py").write_text("x = 1\r\nx += 1\n")      # a carriage return broke the patch once (t680)
    (repo / "gone.py").unlink()
    (repo / "blob.bin").write_bytes(bytes(range(255, -1, -1)))
    (repo / "new.py").write_text("z = 3\n")                 # untracked
    return repo


def test_the_clone_holds_the_working_tree_bytes():
    d = H.sandbox("clonetest-")
    try:
        repo, orig = fixture(d), H.REPO
        H.REPO = repo
        try:
            out = Path(d) / "out"
            out.mkdir()
            clone, err = H.build_clone(out)
        finally:
            H.REPO = orig
        assert clone is not None, err
        for rel in ("kept.py", "blob.bin", "new.py"):
            assert (clone / rel).read_bytes() == (repo / rel).read_bytes(), rel
        assert not (clone / "gone.py").exists()
        assert git(clone, "status", "--porcelain") == "", "the carried tree is committed in the clone"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_a_ram_backed_dir_is_refused():
    shm = Path("/dev/shm")
    if not H._is_tmpfs(shm):
        print("  (skip: /dev/shm is not tmpfs on this box)")
        return
    with tempfile.TemporaryDirectory(dir=shm) as d:
        clone, err = H.build_clone(d)
        assert clone is None and "tmpfs" in err, (clone, err)
        assert not (Path(d) / "ai-workspace").exists(), "refused before cloning anything"


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for n, f in tests:
        try:
            f()
            print(f"PASS {n}")
        except Exception as e:                                      # noqa: BLE001
            bad += 1
            print(f"FAIL {n}: {type(e).__name__}: {e}")
    print(f"{len(tests) - bad} passed, {bad} failed")
    sys.exit(1 if bad else 0)
