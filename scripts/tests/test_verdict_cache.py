#!/usr/bin/env python3
"""The related-tests gate is paid once per tree state, not once per attempt (2026-10-30).

Jas: "I want multiple session ends to happen at the same time with no issues... I want to only do what's
necessary". The gate itself is not negotiable -- a commit must not carry a break -- but paying 900 s again for
byte-identical inputs is what turned one session end into three hours on 2026-10-30 (04:35 to 04:50 paying it,
then again from 05:08).

So this suite pins the two halves that make a cache safe:
  * the KEY moves when anything a suite can see moves (HEAD, a tracked .py, an untracked .py, the suite list);
  * the reuse is a reuse, not a bypass: the same answer comes back, and a changed module re-runs.

The whole tree here is a THROWAWAY git repo. Nothing touches the live checkout: a test that edits the checkout to
prove a cache invalidates is a test that can break the box it is testing on.
RED ON MAIN: related_tests has no cache, so the second run of an unchanged tree calls run() again --
  BAD  an unchanged tree re-ran it instead of reusing the verdict (RED ON MAIN)  (1 call(s) the second time)
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HERE / "scripts"))
import test_verdict_cache as tvc          # noqa: E402
import related_tests as rt                # noqa: E402

fails = []


def check(name, ok, detail=""):
    print(f"  {'ok ' if ok else 'BAD'}  {name}" + ("" if ok else f"  ({detail})"))
    if not ok:
        fails.append(name)


def git(repo, *a):
    return subprocess.run(["git", *a], cwd=str(repo), capture_output=True, text=True).stdout


SUITE = "test_fabricated.py"

with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as cd:
    os.environ["TEST_VERDICT_CACHE"] = cd          # never the live cache
    repo = Path(td) / "r"
    (repo / "scripts" / "tests").mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "scripts" / "thing.py").write_text("X = 1\n")
    (repo / "scripts" / "tests" / SUITE).write_text("# a suite\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base")

    # --- the key ---------------------------------------------------------------------------------
    k0 = tvc.state_digest(repo, [SUITE])
    check("the same tree gives the same key", tvc.state_digest(repo, [SUITE]) == k0)
    (repo / "scripts" / "thing.py").write_text("X = 2\n")            # an UNSTAGED edit
    k1 = tvc.state_digest(repo, [SUITE])
    check("an unstaged edit to a tracked module changes the key", k1 != k0,
          "a suite imports what is on DISK, not what is staged")
    (repo / "scripts" / "newmod.py").write_text("Y = 1\n")           # an UNTRACKED file
    k2 = tvc.state_digest(repo, [SUITE])
    check("an untracked module changes the key", k2 != k1, "a new file can shadow a module")
    check("the suite list is part of the key", tvc.state_digest(repo, []) != tvc.state_digest(repo, [SUITE]))
    before = tvc.state_digest(repo, [SUITE])
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "move")
    check("a new HEAD changes the key", tvc.state_digest(repo, [SUITE]) != before)


    # --- an unreadable state is never reused ------------------------------------------------------
    plain = Path(td) / "not-a-repo"
    plain.mkdir()
    (plain / "scripts").mkdir()
    (plain / "scripts" / "thing.py").write_text("X = 1\n")
    try:
        tvc.state_digest(plain, [SUITE])
        ok = False
    except tvc.CannotDetermine:
        ok = True
    check("a repo whose state cannot be read raises instead of returning a key", ok,
          "two unreadable trees would otherwise share one key and inherit each other's verdicts")

    # --- the ceiling is part of the key -----------------------------------------------------------
    ceil = repo / "workspace" / "ceiling.json"
    ceil.parent.mkdir(exist_ok=True)
    ceil.write_text('{"allowed_to_fail": []}')
    k_before = tvc.state_digest(repo, [SUITE], ceiling=ceil)
    ceil.write_text('{"allowed_to_fail": ["x.py"]}')
    check("a ceiling edit changes the key", tvc.state_digest(repo, [SUITE], ceiling=ceil) != k_before,
          "the same red is blocking debt or known debt depending on this file")

    # --- the store --------------------------------------------------------------------------------
    key = tvc.state_digest(repo, [SUITE])
    check("a miss is None, never an empty mapping (an empty dict reads as 'nothing failed')",
          tvc.get(key) is None)
    check("a store takes", tvc.put(key, {SUITE: 1}, [SUITE]))
    check("a red comes back red, unchanged", tvc.get(key) == {SUITE: 1})
    tvc.put(key, {SUITE: 0}, [SUITE])
    check("a green comes back green, because what was measured is what is stored", tvc.get(key) == {SUITE: 0})
    check("an expired verdict is not reused", tvc.get(key, ttl_hours=-1) is None)
    tvc._path(key).write_text("{not json", encoding="utf-8")
    check("a corrupt entry is a miss, not a crash", tvc.get(key) is None)
    tvc.put(key, {})
    check("an empty measurement is not stored", tvc.get(key) is None)

    # --- the wiring: related_tests must ASK the cache ----------------------------------------------
    calls = []

    def fake_run(name):
        calls.append(name)
        return 1, "  RED: fabricated"            # always red, so a bypass shows up as a re-run

    real_run, real_related, real_repo = rt.run, rt.related, rt.REPO
    rt.run, rt.related, rt.REPO = fake_run, (lambda paths: [SUITE]), repo
    try:
        rc1 = rt.main(["scripts/thing.py"])
        first = len(calls)
        rc2 = rt.main(["scripts/thing.py"])
        second = len(calls) - first
        (repo / "scripts" / "thing.py").write_text("X = 3\n")        # the tree moves
        calls.clear()
        rc3 = rt.main(["scripts/thing.py"])
        after_edit = len(calls)
    finally:
        rt.run, rt.related, rt.REPO = real_run, real_related, real_repo
    check("the first run actually ran the suite", first >= 1, f"{first} call(s)")
    check("an unchanged tree re-ran it instead of reusing the verdict (RED ON MAIN)",
          second == 0, f"{second} call(s) the second time")
    check("both runs report the same answer", rc1 == rc2 == 1, f"{rc1} vs {rc2}")
    check("a changed module re-runs rather than reusing", after_edit >= 1, f"{after_edit} call(s)")
    check("and the re-run still reports red", rc3 == 1, str(rc3))

print("test_verdict_cache:", "FAILED" if fails else "ok")
sys.exit(1 if fails else 0)
