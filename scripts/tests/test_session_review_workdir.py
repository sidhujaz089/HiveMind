#!/usr/bin/env python3
"""t1535 -- a session-review model call must not write its prompt/answer into a swept directory.

Evidence (RCA dfb5071996cd, 2026-10-21; both attempts answered and both lost the answer):
  workspace/run-logs/dispatch/dfb5071996cd.json--rca--swift--1792538284.log
      -> done_reason=stop 13 min 21750 chars (11 tool round(s))
      FileNotFoundError: [Errno 2] No such file or directory:
        '/home/user/.cache/hivemind-tmp/session-review-j3b_3qjd/answer.md'
  workspace/run-logs/dispatch/dfb5071996cd.json--rca--swift--1792536369.log
      FileNotFoundError: ... '/home/user/.cache/hivemind-tmp/session-review-45fahdj_/prompt.txt'

The prompt/answer lived in tempfile.mkdtemp(prefix="session-review-") -- the shared TMPDIR, which for
a scheduler-started job is ~/.cache/hivemind-tmp, a root the temp sweepers reclaim. A model call that
runs 13 to 39 minutes outlives the directory it was told to write its answer into, and session_review
then reports the missing answer as an empty string. The fix moves that directory into the repository,
where no sweeper has a claim on it.
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import session_review as sr  # noqa: E402

# The roots a sweep reclaims (scripts/sweep.py lists both). A work dir inside either one is a record
# whose lifetime is decided by a third party -- which is what lost dfb5071996cd its 21,750-char answer.
SWEPT = [Path(tempfile.gettempdir()).resolve(), (Path.home() / ".cache" / "hivemind-tmp").resolve()]


def test_WORK_is_a_repository_directory_no_sweeper_owns():
    work = Path(sr.WORK).resolve()
    assert work == (sr.REPO / "workspace" / "session-reviews" / "work"), \
        f"WORK must be the repo's session-reviews work dir, found {work}"
    inside = [s for s in SWEPT if work == s or s in work.parents]
    assert not inside, \
        f"WORK={work} sits inside a swept temp root {inside}: a model call longer than the sweep " \
        "interval loses the file its answer is written to"


def test_workdir_creates_one_fresh_directory_per_call_under_WORK():
    saved, sr.WORK = sr.WORK, Path(tempfile.mkdtemp()) / "work"
    root = sr.WORK.parent
    try:
        a, b = sr.workdir(), sr.workdir()
        assert isinstance(a, Path), f"workdir() must return a Path, got {type(a).__name__}"
        assert a.parent == sr.WORK, f"workdir() must sit directly under WORK: {a} vs {sr.WORK}"
        assert a.is_dir(), f"workdir() must create the directory, not just name it: {a}"
        assert b.is_dir() and b != a, f"two calls must not share one directory: {a} == {b}"
        (a / "prompt.txt").write_text("x", encoding="utf-8")
        assert (a / "prompt.txt").read_text(encoding="utf-8") == "x", \
            "a prompt written into workdir() must be readable from it"
    finally:
        sr.WORK = saved
        shutil.rmtree(root, ignore_errors=True)


def main():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as e:  # noqa: BLE001 -- the reason IS the record; never a bare pass
            bad += 1
            print(f"FAIL {name}: {type(e).__name__}: {e}")
    print(f"{len(tests) - bad}/{len(tests)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
